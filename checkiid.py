#!/usr/bin/python
#
# IID Checker
#
# Utility that checks to make sure IIDs are changed when an IDL file is modified.
# Designed to be used as a pre-qfinish or pre-push hook in hg.
#
# Usage: <diff/patch file output > | checkiid.py $(hg root)
#
# You can add this to your .hgrc file with the lines:
# [hooks]
# pre-qfinish = hg export qtip | checkiid $(hg root)
#
# NOTE: To retrieve diffs of certain files, you can use hg diff include/exclude
#       notation:
#
#       hg diff -r <startrev> -r <endrev> -I "**.idl"
#
# WARNING: Your tree must be in the same state as <endrev>. To achieve this,
#          you must run: hg update -r <endrev> BEFORE running the script!
#
import re
import sys
import os.path
import argparse
import tempfile
import difflib
from prettyprinter import PrettyPrinter
from idlutils import IDLDescriptor
from idlutils import SpecialBlockType
from idlutils import SpecialBlockRange

# Use to turn on debugging output
DEBUG = False

# Verbose output switch
VERBOSE = False

# Whether or not color formatting should be turned on
COLOR = True

# Path to reference "output" file when performing unit test in test mode.
gOutputTestPath = None

# Command-line argument parser
gParser = None

# Printing utility vehicle
gPrinter = None

# Simple class representing an interface or dictionary name.
# class ChangeContainer:
#  def __init__(self, aName, aType='interface'):
#    self.mType = aType
#    self.mName = aName
#
#  def __str__(self):
#    return self.getName()
#
#  def getName(self):
#    return self.mName
#
#  def isInterface(self):
#    return self.mType == 'interface'
#
#  def isDictionary(self):
#    return self.mType == 'dictionary'

# Detect whether a line (in a series of lines from diff output) indicates the
# start of processing on an IDL file.
#
# @param aLine A line to check
#
# @returns True, if the line indicates following lines are within an IDL file,
#          False, otherwise.
def isStartOfIDLFile(aLine):
  if extractIDLFileName(aLine):
    return True

  return False

# Detect whether or not a line is the addition of a new interface, based on
# data collected from the line, the current interface (if one has been seen)
# and the IDL file of which the interface is a part.
#
# @param aLine The line to check. This line must represent an interface
#        definition line for this method to return true.
# @param aCurrentInterface The name of the interface currently being processed.
#        Can be None, but this will automatically result in a return value of
#        False.
# @param aIDLFilePath The path on the filesystem to the IDL file where
#        aCurrentInterface is defined. If this is None, then this method will
#        return False.
# @param aLineRange A tuple containing the start and end lines (the range) from
#        which we last saw the removal of the IID and interface. Can be None,
#        but this will result in searching the entire IDL file for the given
#        interface name.
#
# @returns True, if the line represents a new interface that was renamed from
#          aCurrentInterface; False otherwise.
def isLineInterfaceRename(aLine, aCurrentInterface, aIDLFilePath, aLineRange=None):
  global gPrinter

  if not aIDLFilePath:
    gPrinter.debug("isLineInterfaceRename: Path nonexistent: " + str(aIDLFilePath))
    return False

  if not aCurrentInterface:
    gPrinter.debug("isLineInterfaceRename: current interface is not set!")
    return False

  if not isInterfaceDefinitionLine(aLine):
    gPrinter.debug("isLineInterfaceRename: aLine is not interface definition line")
    return False

  # If this line is an interface definition line, and a current interface is
  # specified, then look at the IDL file path at the given lines, and see if
  # they still contain the old interface name.
  try:
    idlFile = open(aIDLFilePath)
  except:
    # We had trouble opening the file, so just return a false value so we report
    # the error.
    gPrinter.debug("isLineInterfaceRename: could not open file path: '" + aIDLFilePath + "'")
    return False

  idlLines = idlFile.readlines()

  if not aLineRange:
    start = 0
    end = len(idlLines)
  else:
    (start, end) = aLineRange

  if not start:
    start = 0

  if not end or end > len(idlLines):
    end = len(idlLines)

  gPrinter.debug("start is: " + str(start) + ", end is: " + str(end))
  idlFile.close()

  counter = start - 1

  gPrinter.debug("idlLines has: " + str(len(idlLines)) + " lines.")
  while (counter < end):
    gPrinter.debug("Attempting to get value within idlLines at line: " + str(counter))
    idlFileLine = idlLines[counter]
    if str(aCurrentInterface) in idlFileLine:
      gPrinter.debug("isLineInterfaceRename: found '" + str(aCurrentInterface) + "' in specified lines!")
      return False
    counter = counter + 1

  return True

# Detect whether or not a line is an addition of a line containing an IDL IID.
#
# @param aLine A line to check
#
# @returns True, if this line represents an addition of a line representing an
#          IID; False, otherwise.
def isLineIIDAddition(aLine):
  newUUID = extractIID(aLine)
  if newUUID and aLine.startswith("+"):
    return True
  return False

# Detect whether or not a line contains a UUID definition.
#
# @param aLine A line to check
#
# @returns True, if this line represents the definition of a UUID (i.e. it's of
#          the form:
#  [(<KEYWORD>,)*)(\s)*uuid(343159D8-B1E9-4464-82FC-B12C7A473CF1)]
#          False, otherwise.
def isLineIIDDefinition(aLine):
  newUUID = extractIID(aLine)
  if newUUID:
    return True
  return False

# Detect whether or not a line is a removal of a line containing an IDL IID.
#
# @param aLine A line to check
#
# @returns True, if this line represents a removal of a line representing an
#          IID; False, otherwise.
def isLineIIDRemoval(aLine):
  oldUUID = extractIID(aLine)
  if oldUUID and aLine.startswith("-"):
    return True
  return False

# Extract the UUID from a line.
#
# @param aLine A line containing an IID
#
# @returns The IID of an addition or removal line in a diff output file, or
#          None, if the line does not contain an IID.
def extractIID(aLine):
  match = re.search("uuid\((.*)\)", aLine)
  if match:
    return match.group(1)

  return None

# Extract the path to an IDL file from a diff output line.
#
# @param aLine The line to extract the path from
# @param aRootPath The root path of the hg repository in which the file is
#        located.
#
# @returns The full path of an IDL file, if the line contains the path (i.e. is
#          the start of an IDL file; None, if the line does not contain such a
#          path.
#
# @see isStartOfIDLFile
def extractIDLFilePath(aLine, aRootPath):
  completePath = aRootPath

  # Find line containing diff --git a/<path>/<idlFilename>.idl
  match = re.search("^diff\ --git\ a/(([a-zA-Z0-9]+/)+([a-zA-Z0-9]+\.idl))\ b/(([a-zA-Z0-9]+/)+([a-zA-Z0-9]+\.idl))", aLine)
  idlPath = None
  if match:
    idlPath = os.path.join(completePath, match.group(4))
  return idlPath

# Extract the filename of an IDL file, without the full path.
#
# @param aLine The line from which to extract the filename. This should be the
#        diff line indicating the start of an IDL file.
#
# @returns The name of the IDL file represented on the line, or None, if one is
#          not found.
#
# @see isStartOfIDLFile
# @see extractIDLFilePath
def extractIDLFileName(aLine):
  path = extractIDLFilePath(aLine, '')
  idlFilename = None
  if (path):
    match = re.search("([a-zA-Z0-9]+\.idl)", path)
    if match:
      idlFilename = match.group(1)

  return idlFilename

# Determine if a line signifies creation of a file.
#
# If a line indicates that the file was created, aLine will be:
#   --- /dev/null
#
# @returns True, if aLine signifies that either the file currently being
#          processed was created, False otherwise.
def doesLineSignifyCreation(aLine):
  match = re.search("^(---)\ /dev/null", aLine)
  if match:
    return True
  return False

# Determine if a line signifies deletion of a file.
#
# If a line indicates that the file was deleted, aLine will be:
#   +++ /dev/null
#
# @returns True, if aLine signifies that either the file currently being
#          processed was deleted, False otherwise.
def doesLineSignifyDeletion(aLine):
  match = re.search("^(\+\+\+)\ /dev/null", aLine)
  if match:
    return True
  return False

# Determine whether or not a line in a diff output file indicates the start of
# processing for a file.
#
# @param aLine The line to check
#
# @returns True, if aLine is a diff line indicating that processing should take
#          place on a different file; False, otherwise.
def isLineStartOfNewFile(aLine):
  match = re.search("^diff\ --git\ a/([a-zA-Z0-9]+/)+([a-zA-Z0-9]+\.[a-zA-Z0-9])+", aLine)
  if match:
    return True
  return False

def isLineConstantExpression(aLine):
  global gPrinter
  if not isAdditionLine(aLine) and not isRemovalLine(aLine):
    return False

  match = re.search("^(\s)*[\+\-](\s)+const(\s)+(.*)", aLine)
  if match:
    gPrinter.debug("Line is constant expression: " + aLine)
    return True
  return False

# Determine whether or not this line represents the addition or removal of a
# comment. This is a non-functional change, and IIDs should not be incremented
# if the only changes to a file are non-functional changes.
#
# @param aLine The line to check
# @param aLineNumber The line number (in the original file, NOT in the diff
#        output) where this line will take effect.
# @param aFilePath The path to the IDL file that this line is changing.
#
# @returns True, if aLine indicates a comment (block or single-line);
#          False, otherwise.
def isLineComment(aLine, aLineNumber, aFilePath):
  global gPrinter
  if not isLineChange(aLine):
    return False

  # To determine this, we check to see if the line starts with '//'
  match = re.search("^[\+\-](\s)*\/\/", aLine)
  if (match):
    return True

  # or is contained within a block comment for a given file.
  ranges = SpecialBlockRange.getRangesForFilePath(aFilePath, gPrinter)

  for myRange in ranges:
    if aLineNumber in myRange:
      gPrinter.debug("Line " + str(aLineNumber) + ": Block comment running from: " + str(myRange.mStartLine) + " to " + str(myRange.mEndLine))
      return True

  return False

# Determine whether a line indicates that an interface is defined in the source
# file.
#
# @param aLine The line to check
#
# @returns True, if the aLine corresponds to the definition of an interface;
#          False, otherwise.
def isInterfaceDefinitionLine(aLine):
  global gPrinter

  if extractInterfaceNameFromDefinitionLine(aLine):
    # If this line ends with a semicolon, then it's not a real interface
    # definition line, but rather a forward declaration. That's not what we want.
    trimmedLine = aLine.rstrip()
    if len(trimmedLine) == 0 or trimmedLine[len(trimmedLine) - 1] == ';':
      gPrinter.debug("Line: " + aLine + " was detected to be a forward declaration.")
      return False

    return True

  return False

# Determine whether a line indicates the context in which changes are being made
# when the diff output is applied as a patch. These lines start with '@@', and
# typically contain the interface name, if an interface is being modified.
#
# @param aLine The line to check.
#
# @returns True, if aLine indicates that context has changed to a new interface;
#          False, otherwise.
def isInterfaceContextLine(aLine):
  if extractInterfaceNameFromContextLine(aLine):
    return True
  return False

# Determine if a line changes the context in which changes take place if the
# diff output is applied as a patch. Context lines begin with '@@' in git diff
# format.
#
# @param aLine The line to check
#
# @returns True, if aLine is a context line;
#          False, otherwise.
def isContextLine(aLine):
  match = re.search("^@@", aLine)
  if match:
    return True
  return False

# Extract an interface name, given a line that is a definition line.
# (e.g. 'interface nsIDOMFileList : nsISupports')
#
# @param aLine The line to check
#
# @returns A ChangeContainer with the interface name, if one was found in the line;
#          None, otherwise.
def extractInterfaceNameFromDefinitionLine(aLine):
  patchExtraction = extractInterfaceName(aLine, "[\+,\-]")
  if patchExtraction:
    return patchExtraction

  return extractInterfaceName(aLine)

# Extract an interface name, given a line that is a context line.
# (e.g. '@@ -14,28 +14,27 @@ interface nsIScriptGlobalObject')
#
# @param aLine The line to check
#
# @returns A ChangeContainer with the interface name, if one was found in the line;
#          None, otherwise.
def extractInterfaceNameFromContextLine(aLine):
  return extractInterfaceName(aLine, "@@(\s)+(.*)(\s)+@@")

# Extract a line number, given a line that is a context line.
# (e.g. '@@ -14,28 +14,27 @@ interface nsIScriptGlobalObject')
#
# @param aLine The line to check
#
# @returns The line number, if one was found in the line;
#          None, otherwise.
def extractLineNumberFromContext(aLine):
  match = re.search("@@(\s)+\-(.*),(.*)(\s)+\+(.*),(.*)(\s)+@@", aLine)
  if (match):
    return int(match.group(2)) - 1
  return 0

# Extract an interface name, given a line and an optional prefix. An interface
# is defined in the following manner:
#
# @param aLine A line (possibly) containing an interface defined in IDL
# @param aContextPrefix A prefix on the line, indicating that it's a context
#        line (e.g. "@@ -71,16 +71,18 @@ interface nsITypeAheadFind : nsISupports")
#        rather than an interface definition line.
#
# NOTE: dictionaries are not required to have IIDs, so they are ignored by this
#       system.
#
# @returns A ChangeContainer with the name of the interface, if one was
#          detected, or None otherwise.
def extractInterfaceName(aLine, aContextPrefix=''):
  # interface [NAME] (optional : followed by one or more items)
  match = re.search("^" + aContextPrefix + "(\s)*interface(\s)+(.*)(\s)*\:(\s)*(.*)", aLine)
  if match:
    baseGroupNum = 6
    baseIndex = 3
    if (aContextPrefix):
      addedGroups = len(match.groups()) - baseGroupNum
      groupNum = baseIndex + addedGroups
      return match.group(groupNum).rstrip()
    else:
      return match.group(baseIndex).rstrip()

#    return ChangeContainer(name, 'interface')
  return None

# Detect whether or not a line from diff output is a line representing a change.
# This is equivalent to isAdditionLine(aLine) || isRemovalLine(aLine).
#
# @param aLine The line to check
#
# @return True, if the line represents either an addition line or removal line
#         in the diff output; False, otherwise.
def isLineChange(aLine):
  if isAdditionLine(aLine) or isRemovalLine(aLine):
    return True
  return False

# Detect whether a line in the diff output represents an addition to a file.
#
# @param aLine A line to check
#
# @returns True, if this line indicates an addition;
#          False, otherwise.
def isAdditionLine(aLine):
  # The line must start with a plus and have only one.
  match = re.search("^[\+]{1}", aLine)
  if not match:
    return False

  match = re.search("^[\+]{3}", aLine)
  if match:
    return False

  return True

def isEndOfInterfaceRemoval(aLine):
  if not isRemovalLine(aLine):
    return False

  match = re.search("^[\-](\s)*\}", aLine)
  if not match:
    return False

  return True

# Detect whether a line in the diff output represents a removal to a file.
#
# @param aLine A line to check
#
# @returns True, if this line indicates a removal;
#          False, otherwise.
def isRemovalLine(aLine):
  # The line must start with a minus and have only one.
  match = re.search("^[\-]{1}", aLine)
  if not match:
    return False

  match = re.search("^[\-]{3}", aLine)
  if match:
    return False

  return True

def extractContentFromChangeLine(aLine):
  if not isRemovalLine(aLine) and not isAdditionLine(aLine):
    return None

  match = re.search("^[\-\+](.*)", aLine)
  if match:
    return match.group(1)
  return None

def updateFileMetadata(aLine, aPrevLineNumber, aLastLineWasRemoval):
    currentLineNumber = aPrevLineNumber + 1
    isRemoval = False

    # if the line is a change line, then we need to adjust our line number
    # counts if we're replacing a line
    if isLineChange(aLine):
      if isAdditionLine(aLine) and aLastLineWasRemoval:
        currentLineNumber = currentLineNumber - 1
        isRemoval = False
      elif isRemovalLine(aLine):
        isRemoval = True
        currentLineNumber = aPrevLineNumber
    return (currentLineNumber, isRemoval)

# Parse a given diff output to get data about which interfaces have been changed
# and whether corresponding IIDs were changed as well.
#
# @param aInputPatch A string containing lines of a diff output 'patch' which
#        needs to be parsed to get the required information.
# @param aRootPath The path to the root hg repository onto which the patch would
#        be applied. This is necessary because the comment-checking code needs
#        to look at actual files, not just the patch file.
#
# @returns A tuple, (interfacesRequiringNewIID, revvedInterfaces,
#          interfaceNameIDLMap), where interfacesRequiringNewIID is a set of
#          interface names that require an IID change, revvedInterfaces is a
#          set of interface names that have already had their IIDs changed, and
#          interfaceNameIDLMap is a map from interface names to IDL file full
#          paths.
def parsePatch(aInputPatch, aRootPath):
  global gPrinter

  currentIDLFile = None
  changedIDLFilePaths = []
  changedIDLFileNames = []
  revvedInterfaces = []
  interfacesRequiringNewIID = []
  interfaceNameIDLMap = {}
  currentInterfaceName = None
  previousInterfaceName = None
  needInterfaceName = False
  foundIIDChangeLine = False
  fileWarningsIssued = []
  interfaceMayBeRemoved = False
  lastUUIDChangeLineSeen = None
  currentInterfaceWasRenamed = False

  # Note that this is NOT the line number in the patch file, but rather the line
  # number where the patch line will take effect for the file in the hg root.
  currentLineNumber = -1
  lastLineWasRemoval = False

  # Patch file line numbers. This is mostly for debugging, but is used for a few
  # other things, as well.
  lineNo = 0

  for line in aInputPatch:
    lineNo = lineNo + 1

    (currentLineNumber, lastLineWasRemoval) = updateFileMetadata(line, currentLineNumber, lastLineWasRemoval)

    idlStart = isStartOfIDLFile(line)

    if idlStart:
      currentIDLFileWasDeleted = False
      interfaceMayBeRemoved = False
      currentInterfaceWasRenamed = False

    if isAdditionLine(line):
      interfaceMayBeRemoved = False

    if not currentInterfaceName:
      needInterfaceName = True

    if doesLineSignifyDeletion(line):
      gPrinter.debug("Current idl file: " + str(currentIDLFile) + " was deleted.")
      currentIDLFileWasDeleted = True

    if currentIDLFileWasDeleted:
      # If the file in question has been removed, then we don't really care
      # about it any longer, so just go ahead to the next one.
      continue

    if isLineChange(line):
      # If this line has no content, or the content is just spaces, then
      # simply skip this line.
      content = extractContentFromChangeLine(line)
      content = content.rstrip()
      if len(content) == 0:

        gPrinter.debug("Line " + str(lineNo) + " was detected to be empty. Continuing.")
        continue

    # if the line is the start of a non-idl file
    if isLineStartOfNewFile(line) and not idlStart:
      gPrinter.debug("Line number " + str(lineNo) + " is start of new file.")
      lastUUIDChangeLineSeen = None
      currentInterfaceWasRenamed = False

      # clear our current interface name
      previousInterfaceName = currentInterfaceName
      currentInterfaceName = None

    if (idlStart):
      gPrinter.debug("Line number " + str(lineNo) + " is start of IDL file.")

      lastUUIDChangeLineSeen = None

      # pop last idl file, if there was one
      currentIDLFile = extractIDLFileName(line)
      currentIDLPath = extractIDLFilePath(line, aRootPath)

      # push idl file path onto changed idl list
      if not currentIDLPath in changedIDLFilePaths:
        changedIDLFilePaths.append(currentIDLPath)

      if not currentIDLFile in changedIDLFileNames:
        changedIDLFileNames.append(currentIDLFile)

      # now that we're in a new file, we need to make sure that we detect the
      # proper interface again

      gPrinter.debug("Interface name WAS: " + str(currentInterfaceName))

      needInterfaceName = True
      previousInterfaceName = currentInterfaceName
      currentInterfaceName = None
      foundIIDChangeLine = False

      gPrinter.debug("Interface now is: " + str(currentInterfaceName))

    if isLineIIDAddition(line):
      # We'll need to put the interface name (as we haven't seen it yet)
      # into the currentInterface variable
      needInterfaceName = True
      previousInterfaceName = currentInterfaceName
      currentInterfaceName = None
      foundIIDChangeLine = True
    elif isLineIIDDefinition(line):
      if isRemovalLine(line):
        interfaceMayBeRemoved = True
      needInterfaceName = True
      previousInterfaceName = currentInterfaceName
      currentInterfaceName = None
      foundIIDChangeLine = False

    # if we need an interface name, and this happens to be the line
    # that defines the interface
    if needInterfaceName and isInterfaceDefinitionLine(line):

      gPrinter.debug("Line number " + str(lineNo) + " is interface definition line and we need one.")

      # extract the interface name
      currentInterfaceName = extractInterfaceNameFromDefinitionLine(line)

      gPrinter.debug("(Line " + str(lineNo) + "): Current interface name is now: " + str(currentInterfaceName))

      # push interface name onto revved interface list (for the previous step)
      if foundIIDChangeLine:
        gPrinter.debug("Appending " + str(currentInterfaceName) + " to revvedInterfaces");

        revvedInterfaces.append(currentInterfaceName)
        foundIIDChangeLine = False

      # indicate that we no longer need an interface name
      needInterfaceName = False

      # add mapping from interface name to idl file
      interfaceNameIDLMap[currentInterfaceName] = currentIDLFile

    # if we didn't need an interface name, but this still happens to be an
    # interface definition line, then we might be in a situation where the
    # interface was renamed.
    if not needInterfaceName and isInterfaceDefinitionLine(line):
      gPrinter.debug("We apparently don't need an interface name, but line: " + str(lineNo) + " was detected to be an interface definition line.")
      if isLineInterfaceRename(line, previousInterfaceName, currentIDLPath, (lastUUIDChangeLineSeen, currentLineNumber + 1)):
        gPrinter.debug("'" + str(currentInterfaceName) + "' was renamed!")
        currentInterfaceWasRenamed = True

    # if this is a context line, then let's extract the line number from it
    if isContextLine(line):

      gPrinter.debug("Line number " + str(lineNo) + " is context line.")

      currentLineNumber = extractLineNumberFromContext(line)

      # modify our current interface to match the one in the context, but only
      # if it's also an interface context line
      if isInterfaceContextLine(line):
        currentInterfaceName = extractInterfaceNameFromContextLine(line)

        gPrinter.debug("Current interface is now: " + str(currentInterfaceName))

        # add mapping from interface name to idl file
        interfaceNameIDLMap[currentInterfaceName] = currentIDLFile

    # Each of these operations is assigned into a variable for clarity when
    # reading the if statement.
    iidRemoval = isLineIIDRemoval(line)

    if iidRemoval:
      lastUUIDChangeLineSeen = currentLineNumber

    shouldIssueWarning = False

    try:
      cmt = isLineComment(line, currentLineNumber, currentIDLPath)
    except:
      # In this case, the file on which we wanted to run was not found, so just
      # assume it's not a comment.
      cmt = False
      shouldIssueWarning = True

    constEx = isLineConstantExpression(line)
    change = isLineChange(line)
    binaryCompat = IDLDescriptor.areDescriptorsInLineAffectingBinaryCompat(line, gPrinter)
    descr = IDLDescriptor.hasDescriptorsInLine(line, gPrinter)

    if shouldIssueWarning & (currentIDLFile not in fileWarningsIssued):
      fileWarningsIssued.append(currentIDLFile)
      gPrinter.warn("'" + str(currentIDLFile) + "' was not found in local repository. Are you sure your repository is at the correct revision?")

    # if line is change to an interface and not a comment, a constant expr, or
    # an IID removal line:
    if binaryCompat or (not currentInterfaceWasRenamed and not descr and not iidRemoval and not cmt and not constEx and change and currentInterfaceName):

      gPrinter.debug("Line number " + str(lineNo) + " with change to interface '" + str(currentInterfaceName) + "' meets qualifications for needing an IID change.")
      gPrinter.debug("binaryCompat: " + str(binaryCompat))
      gPrinter.debug("currentInterfaceWasRenamed: " + str(currentInterfaceWasRenamed))
      gPrinter.debug("change: " + str(change))
      gPrinter.debug("comment: " + str(cmt))
      gPrinter.debug("currentInterfaceName: " + str(currentInterfaceName))
      gPrinter.debug("iid removal: " + str(iidRemoval))
      gPrinter.debug("const expr: " + str(constEx))

      # push interface name onto required revs list
      if currentInterfaceName not in interfacesRequiringNewIID:
        interfacesRequiringNewIID.append(currentInterfaceName)

    # Finally, if we just saw the end of an interface's definition, and there
    # were no additions (only removals), then we don't need to increment the
    # IID of this interface, because it's being removed completely.
    if isEndOfInterfaceRemoval(line) and interfaceMayBeRemoved and currentInterfaceName in interfacesRequiringNewIID:
      interfacesRequiringNewIID.remove(currentInterfaceName)
      interfaceMayBeRemoved = False

  return (interfacesRequiringNewIID, revvedInterfaces, interfaceNameIDLMap)

def parseArguments():
  global gParser, DEBUG, VERBOSE, COLOR, gOutputTestPath

  if not gParser:
    createParser()

  parsed = gParser.parse_args(sys.argv[1:])

  if parsed.verbose:
    VERBOSE = True

  if parsed.debug:
    DEBUG = True

  if parsed.nocolor:
    COLOR = False

  if not parsed.repo:
    gParser.print_help()
    exit(0)

  if parsed.testpath:
    gOutputTestPath = parsed.testpath[0]

  if parsed.inputfile == 'stdin':
    return (sys.stdin, parsed.repo[0])

  try:
    inputFile = open(parsed.inputfile)
  except:
    gParser.print_help()
    print("ERROR: Unable to open file '" + str(parsed.inputfile) + "'!")
    exit(0)

  return (inputFile, parsed.repo[0])

def createParser():
  global gParser

  if not gParser:
    gParser = argparse.ArgumentParser(description='''
        Check changed interfaces in a given diff output, verifying that all
        interfaces that were changed have an associated IID change.
    ''', add_help=True)
    gParser.add_argument('-V', '--verbose', action='store_true', dest='verbose',
                         help='Output more information that errors (i.e. all interfaces checked).')
    gParser.add_argument('-d', '--debug', action='store_true', dest='debug',
                         help='Print debugging information while running.')
    gParser.add_argument('-t', metavar=('<reference output file>'), action='store',
                        dest="testpath", nargs=1, help="Perform a unit test and compare output against a reference file.")
    gParser.add_argument('repo', help='Path to hg repository from where diff was taken', nargs=1)
    gParser.add_argument('inputfile', help='Path to a patch file on which to operate', nargs='?', default="stdin")
    gParser.add_argument('-n', '--no-color', action="store_true", dest="nocolor",
                         help='Disable output of colored ANSI text (helpful for scripts)')

def main(aRootPath, aFile):
  global gPrinter, gOutputTestPath

  ### initialization stage ###
  implicitJs = IDLDescriptor("implicit_jscontext", True)
  nostdcall = IDLDescriptor("nostdcall", True)
  notxpcom = IDLDescriptor("notxpcom", True)
  optionalArgc = IDLDescriptor("optional_argc", True)
  IDLDescriptor.kDescriptorList = [implicitJs, nostdcall, notxpcom, optionalArgc]

  ### parsing stage ###
  (interfacesRequiringNewIID, revvedInterfaces, interfaceNameIDLMap) = parsePatch(aFile, aRootPath)
  unrevvedInterfaces = []

  ### checking stage ###
  # for each interface in required revs list:
  for interface in interfacesRequiringNewIID:
    # if the interface name is in revved interface list:
    if interface in revvedInterfaces:
      # report that we saw the interface and that it has an IID change

      gPrinter.info("Interface '" + str(interface) + "' has changes and a modified IID. Looks good.")

      # then just continue, because this interface change is good
      continue

    else:
      # add interface name to unrevved interface list
      unrevvedInterfaces.append(interface)

  ### reporting stage ###
  # if there is at least one interface that has an unrevved IID:
  if len(unrevvedInterfaces) > 0:
    if gOutputTestPath:
      tempFile = tempfile.TemporaryFile(prefix="checkiid-test-file-log")

    for interface in unrevvedInterfaces:
      # report that interface and the file that it's a part of
      interfaceFilename = interfaceNameIDLMap[interface]
      message = "Interface '" + str(interface) + "', in file '" + interfaceFilename + "' needs a new IID"

      if not gOutputTestPath:
        gPrinter.error(message)
      else:
        gPrinter.debug("Printing '" + str(message) + "' to tempFile...")
        tempFile.write(message + "\n")

  ## OPTIONAL Unit Test Mode ###
  if gOutputTestPath:
    try:
      tempFile.seek(0)
      tempLines = tempFile.readlines()
      gPrinter.debug("tempFile lines: " + str(tempLines))
      tempFile.close()  # this deletes the temporary file

    except:
      hitException = True
      tempLines = []

    refFile = open(gOutputTestPath, "r")
    gPrinter.debug("Opening '" + str(gOutputTestPath) + "' as reference file")
    refLines = refFile.readlines()
    gPrinter.debug("RefLines: " + str(refLines))
    refFile.close()

    invalidCompFound = False
    reftestLineNo = 0

    # scan through all lines and remove those that are prefixed with '#'
    refLinesToRemove = []
    for curRefLine in refLines:
      match = re.search("^#", curRefLine)
      if (match):
        gPrinter.debug("Removing line from refFile: " + str(curRefLine))
        refLinesToRemove.append(curRefLine)

    for lineToRemove in refLinesToRemove:
      refLines.remove(lineToRemove)

    for curInputLine in tempLines:
      gPrinter.debug("input line: " + curInputLine)
    gPrinter.debug("Number of input lines: " + str(len(tempLines)))
    gPrinter.debug("Number of reference lines: " + str(len(refLines)))
    if len(tempLines) != len(refLines):
      invalidCompFound = True
      print("Expected " + str(len(refLines)) + " lines of output, Found: " + str(len(tempLines)) + " lines of output.")
    else:
      gPrinter.debug("Comparing line-by-line...")
      for line1 in refLines:
        gPrinter.debug("Reference line was: " + str(line1))
        line2 = tempLines[reftestLineNo]
        gPrinter.debug("Input line was: " + str(line2))
        if line1 is line2:
          invalidCompFound = True
          print("Expected: " + str(line1) + ", Found: " + str(line2))
        reftestLineNo = reftestLineNo + 1

    if invalidCompFound:
      print ("UNEXPECTED-TEST-FAIL")
      sys.exit(1)
    else:
      print("TEST-PASS")
      sys.exit(0)

def runMain():
  global gPrinter

  (patchFile, rootPath) = parseArguments()

  # setup our printing utility vehicle
  gPrinter = PrettyPrinter(COLOR, DEBUG, VERBOSE)

  main(rootPath, patchFile)

  if not patchFile == sys.stdin:
    patchFile.close()

if __name__ == '__main__':
  runMain()
