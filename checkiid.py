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

# Use to turn on debugging output
DEBUG = False

# Verbose output switch
VERBOSE = False

# Whether or not color formatting should be turned on
COLOR = True

# Global list of IDL file paths -> block comment ranges
gFilePathToCommentRangeMap = {}

# Global list of IDL descriptors
gDescriptorList = []

# Path to reference "output" file when performing unit test in test mode.
gOutputTestPath = None

# Command-line argument parser
gParser = None

# Printing utility vehicle
gPrinter = None

# This is a class that allows us to print prettier output to the command line.
class PrettyPrinter:
  INFO = '\033[92m'
  DEBUG = '\033[94m'
  WARNING = '\033[93m'
  ERROR = '\033[91m'
  ENDCOLOR = '\033[0m'

  def __init__(self, aEnableColor=False, aDebugEnabled=False, aVerboseEnabled=False):
    self.mColorEnabled = aEnableColor
    self.mDebugEnabled = aDebugEnabled
    self.mVerboseEnabled = aVerboseEnabled

  def debug(self, aMessage):
    self.printColor('debug', aMessage)

  def warn(self, aMessage):
    self.printColor('warn', aMessage)

  def error(self, aMessage):
    self.printColor('error', aMessage)

  def info(self, aMessage):
    self.printColor('info', aMessage)

  def isColorDisabled(self):
    return not self.mColorEnabled

  def printNoColor(self, aType, aMessage):
    if aType == 'warn':
        print ("WARNING: " + str(aMessage))
    elif aType == 'error':
        print ("ERROR: " + str(aMessage))
    elif aType == 'info':
        if self.mVerboseEnabled:
          print ("INFO: " + str(aMessage))
    elif aType == 'debug':
      if self.mDebugEnabled:
        print ("DEBUG: " + str(aMessage))
    else:
      # just print the message verbatim then, with no additions
      print(aMessage)

  def printColor(self, aType, aMessage):
    if self.isColorDisabled():
      self.printNoColor(aType, aMessage)
      return

    if aType == 'warn':
      print (self.WARNING + "WARNING: " + self.ENDCOLOR + str(aMessage))
    elif aType == 'error':
      print (self.ERROR + "ERROR: " + self.ENDCOLOR + str(aMessage))
    elif aType == 'info':
      if (self.mVerboseEnabled):
        print (self.INFO + "INFO: " + self.ENDCOLOR + str(aMessage))
    else:
      # we assume that the type was 'debug' then
      if self.mDebugEnabled:
        print (self.DEBUG + "DEBUG: " + self.ENDCOLOR + str(aMessage))

class IDLDescriptor:
  def __init__(self, aToken, aAffectsBinaryCompat):
    self.mToken = aToken
    self.mBinaryCompat = aAffectsBinaryCompat

  def affectsBinaryCompatibility(self):
    return self.mBinaryCompat

  def getToken(self):
    return self.mToken

  def isInLine (self, aLine):
    global gPrinter
    match = re.search("^(\s)*[\+\-](\s)*\[(.*)](.*)", aLine)
    if match:
      splitAttrs = match.group(3).split(",")
      for attr in splitAttrs:
        gPrinter.debug("Found descriptor: " + attr)
        if attr == self.mToken:
          return True
    return False

  def hasDescriptorsInLine(aLine):
    global gDescriptorList
    for desc in gDescriptorList:
      if desc.isInLine(aLine):
        return True
    return False

  def areDescriptorsInLineAffectingBinaryCompat(aLine):
    global gDescriptorList

    if not IDLDescriptor.hasDescriptorsInLine(aLine):
      return False

    for desc in gDescriptorList:
      if desc.affectsBinaryCompatibility:
        gPrinter.debug("Descriptor: " + desc.getToken() + " affects binary compatibility.")
        return True

    gPrinter.debug("No descriptors found affecting binary compatibility.")
    return False

  areDescriptorsInLineAffectingBinaryCompat = staticmethod(areDescriptorsInLineAffectingBinaryCompat)
  hasDescriptorsInLine = staticmethod(hasDescriptorsInLine)

class SpecialBlockType:
  def __init__(self, aStartToken, aEndToken):
    self.mStartToken = aStartToken
    self.mEndToken = aEndToken

  def getStartToken(self):
    return self.mStartToken

  def getEndToken(self):
    return self.mEndToken

  def __str__(self):
    return "[SpecialBlockType (" + str(self.mStartToken) + ")]"

  def __equals__(self, aOther):
    if self.mStartToken == aOther.mStartToken and self.mEndToken == aOther.mEndToken:
      return True
    return False

  # Determines whether a line is the start of an IDL block that requires special
  # handling (e.g. a comment or language specific code block).
  #
  # @param aLine The line to check
  #
  # @returns True, if the line is the start of an IDL block of a given type,
  #          False otherwise
  def isStartOfSpecialBlock(self, aLine):
    # a line is a start of a block comment if it has the start token at the
    # beginning of the line
    match = re.match("^(\s)*" + self.mStartToken, aLine)
    if match:
      return True
    return False

  def containsSpecialBlock(self, aLine):
    match = re.match("^(\s)*(.*)" + self.mStartToken + "(.*)" + self.mEndToken + "(\s)*(.*)", aLine)
    if match:
      return True
    return False

  # Determines whether a line is the end of an IDL block that requires special
  # handling (e.g. a comment or language specific code block)
  #
  # @param aLine The line to check
  #
  # @returns True, if the line is the end of an IDL block of a given type,
  #          False otherwise
  def isEndOfSpecialBlock(self, aLine):
    # a line is the end of a block comment if it has */ at the
    # end of the line
    regex = "(.*)" + self.mEndToken + "(\s)*$"
    match = re.match(regex, aLine)
    if match:
      return True
    return False

# A SpecialBlockRange is composed of two numerals indicating lines at which
# the range starts and ends, respectively, as well as a member variable
# indicating the file from which the range was generated.
#
# The following are cases that the SpecialBlockRange handles:
# Block comment range: StartingToken: /*, Ending token: */
# C++-specific range: Starting token {%C++, Ending token: %}
class SpecialBlockRange:
  def __init__(self, aStart, aEnd, aFilePath):
    self.mStartLine = aStart
    self.mEndLine = aEnd
    self.mFilePath = aFilePath

  # A SpecialBlockRange (x,y) CONTAINS a line number, l, iff l>=x AND l<=y.
  def __contains__(self, x):
    if x >= self.mStartLine and x <= self.mEndLine:
      return True
    return False

  def __len__(self):
    return self.getEndLine() - self.getStartLine() + 1

  def __str__(self):
    return "[SpecialBlockRange (" + str(self.mStartLine) + ", " + str(self.mEndLine) + ")]"

  def getStartLine(self):
    return self.mStartLine

  def getEndLine(self):
    return self.mEndLine

  def getFilePath(self):
    return self.mFilePath

  def getRangesForFilePath(aFilePath):
    if not aFilePath in gFilePathToCommentRangeMap:
      SpecialBlockRange.findAllSpecialBlocksForFile(aFilePath)

    return gFilePathToCommentRangeMap[aFilePath]

  # Find all the special block ranges for a file, given the file path.aFilePath
  # This method does not return anything, instead it populates the
  # gFilePathToCommentRangeMap global.
  #
  # This will raise an IOError if aFilePath cannot be found. This usually only
  # happens if the hg repository is in a different state/commit than what the
  # script is expecting (the end revision is different).
  #
  # @param aFilePath A string representing the path on disk of the file to
  #        check.
  def findAllSpecialBlocksForFile(aFilePath):
    global gFilePathToCommentRangeMap, gPrinter

    gPrinter.debug("Starting findAllSpecialBlocksForFile")

    if not aFilePath in gFilePathToCommentRangeMap.keys():
      gFilePathToCommentRangeMap[aFilePath] = []

    parseFile = open(aFilePath)

    lineNo = 0
    commentType = SpecialBlockType("\/\*", "\*\/")
    cppType = SpecialBlockType("\%{\s*C\+\+", "\%\}")

    # This is a stack of (SpecialBlockType, integer) tuples that represents
    # the last type seen along with the line number at which it was seen, so
    # we can correctly handle nested types.
    blockStack = []

    # for each line in the file path
    for line in parseFile:
      lineNo = lineNo + 1

      # if the line contains a comment block, then we just ignore it right now
      # because we're not smart enough to handle offsets within a comment block.
      if commentType.containsSpecialBlock(line):
        gPrinter.debug("(" + aFilePath + ", Line " + str(lineNo) + "): A comment block starts and ends on this line.")
        continue

      # if the line is the start of a special block range, document it
      if (commentType.isStartOfSpecialBlock(line)):
        gPrinter.debug("Pushing to stack: " + str(commentType) + ", lastLineNo: " + str(lineNo))

        blockStack.append((commentType, lineNo))

      # if the line is the end of a block comment, create a SpecialBlockRange
      # and add it to the map
      if (commentType.isEndOfSpecialBlock(line)):
        if len(blockStack) == 0:
          gPrinter.debug("(" + aFilePath + ", Line " + str(lineNo) + "): An error occurred while trying to pop from the stack.")

          # Right now, we just skip this line because we don't know what to do with it otherwise.
          # Once Ticket #90 (http://www.glasstowerstudios.com/trac/JMozTools/ticket/90)
          # is fixed, we can do something more intelligent here.
          continue

        (lastSeenType, lastLineNo) = blockStack.pop()

        gPrinter.debug("Popped from stack. Last seen type: " + str(lastSeenType) + ", lastLineNo: " + str(lastLineNo))

        if lastSeenType == commentType:
          blockRange = SpecialBlockRange(lastLineNo, lineNo, aFilePath)
          gFilePathToCommentRangeMap[aFilePath].append(blockRange)
        else:
          gPrinter.debug("Pushing to stack: " + str(lastSeenType) + ", lastLineNo: " + str(lastLineNo))

          blockStack.push((lastSeenType, lastLineNo))

      # if the line is the start of a cpp-specific code block
      if cppType.isStartOfSpecialBlock(line):

        gPrinter.debug("Pushing to stack: " + str(cppType) + ", lastLineNo: " + str(lineNo))

        blockStack.append((cppType, lineNo))

      if cppType.isEndOfSpecialBlock(line):
        if len(blockStack) == 0:
          gPrinter.debug("(" + aFilePath + ", Line " + str(lineNo) + "): An error occurred while trying to pop from the stack.")

        (lastSeenType, lastLineNo) = blockStack.pop()

        gPrinter.debug("Popped from stack. Last seen type: " + str(lastSeenType) + ", lastLineNo: " + str(lastLineNo))

        if lastSeenType == cppType:
          blockRange = SpecialBlockRange(lastLineNo, lineNo, aFilePath)
          gFilePathToCommentRangeMap[aFilePath].append(blockRange)
        else:
          gPrinter.debug("Pushing to stack: " + str(lastSeenType) + ", lastLineNo: " + str(lastLineNo))
          blockStack.push((lastSeenType, lastLineNo))

    parseFile.close()

  # Make the getRanges and findAllComments methods static.
  findAllSpecialBlocksForFile = staticmethod(findAllSpecialBlocksForFile)
  getRangesForFilePath = staticmethod(getRangesForFilePath)

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

  idlFile.close()

  counter = start

  while (counter <= end):
    idlFileLine = idlLines[counter]
    if aCurrentInterface in idlFileLine:
      gPrinter.debug("isLineInterfaceRename: found '" + aCurrentInterface + "' in specified lines!")
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
  ranges = SpecialBlockRange.getRangesForFilePath(aFilePath)

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
# @returns The interface name, if one was found in the line;
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
# @returns The interface name, if one was found in the line;
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
# @returns The name of the interface, if one was detected, or None otherwise.
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
  global gPrinter, gDescriptorList

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

      gPrinter.debug("(Line " + str(lineNo) + "): Current interface name is now: " + currentInterfaceName)

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
        gPrinter.debug("'" + currentInterfaceName + "' was renamed!")
        currentInterfaceWasRenamed = True

    # if this is a context line, then let's extract the line number from it
    if isContextLine(line):

      gPrinter.debug("Line number " + str(lineNo) + " is context line.")

      currentLineNumber = extractLineNumberFromContext(line)

      # modify our current interface to match the one in the context, but only
      # if it's also an interface context line
      if isInterfaceContextLine(line):
        currentInterfaceName = extractInterfaceNameFromContextLine(line)

        gPrinter.debug("Current interface is now: " + currentInterfaceName)

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
    binaryCompat = IDLDescriptor.areDescriptorsInLineAffectingBinaryCompat(line)
    descr = IDLDescriptor.hasDescriptorsInLine(line)

    if shouldIssueWarning & (currentIDLFile not in fileWarningsIssued):
      fileWarningsIssued.append(currentIDLFile)
      gPrinter.warn(str(currentIDLFile) + "' was not found in local repository. Are you sure your repository is at the correct revision?")

    # if line is change to an interface and not a comment, a constant expr, or
    # an IID removal line:
    if binaryCompat or (not currentInterfaceWasRenamed and not descr and not iidRemoval and not cmt and not constEx and change and currentInterfaceName):

      gPrinter.debug("Line number " + str(lineNo) + " with change to interface '" + currentInterfaceName + "' meets qualifications for needing an IID change.")
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
  global gPrinter, gDescriptorList, gOutputTestPath

  ### initialization stage ###
  implicitJs = IDLDescriptor("implicit_jscontext", True)
  nostdcall = IDLDescriptor("nostdcall", True)
  notxpcom = IDLDescriptor("notxpcom", True)
  optionalArgc = IDLDescriptor("optional_argc", True)
  gDescriptorList = [implicitJs, nostdcall, notxpcom, optionalArgc]

  ### parsing stage ###
  (interfacesRequiringNewIID, revvedInterfaces, interfaceNameIDLMap) = parsePatch(aFile, aRootPath)
  unrevvedInterfaces = []

  ### checking stage ###
  # for each interface in required revs list:
  for interface in interfacesRequiringNewIID:
    # if the interface name is in revved interface list:
    if interface in revvedInterfaces:
      # report that we saw the interface and that it has an IID change

      gPrinter.info("Interface '" + interface + "' has changes and a modified IID. Looks good.")

      # then just continue, because this interface change is good
      continue

    else:
      # add interface name to unrevved interface list
      unrevvedInterfaces.append(interface)

  ### reporting stage ###
  # if there is at least one interface that has an unrevved IID:
  if len(unrevvedInterfaces) > 0:
    if gOutputTestPath:
      tempFile = open(os.path.join(tempfile.gettempdir(), "checkiid-test-file.log"), "w+")

    for interface in unrevvedInterfaces:
      # report that interface and the file that it's a part of
      interfaceFilename = interfaceNameIDLMap[interface]
      message = "Interface '" + interface + "', in file '" + interfaceFilename + "' needs a new IID"

      if not gOutputTestPath:
        gPrinter.error(message)
      else:
        tempFile.write(message + "\n")

    if gOutputTestPath:
      tempFile.close()

  ## OPTIONAL Unit Test Mode ###
  if gOutputTestPath:
    tempFile = open(os.path.join(tempfile.gettempdir(), "checkiid-test-file.log"), "w+")
    refFile = open(gOutputTestPath)
    refLines = refFile.readlines()
    tempLines = tempFile.readlines()

    invalidCompFound = False
    reftestLineNo = 0

    if len(tempLines) != len(refLines):
      invalidCompFound = True
      print("Expected " + str(len(refLines)) + " lines of output, Found: " + str(len(tempLines)) + " lines of output.")
    else:
      for line1 in refLines:
        line2 = tempLines[reftestLineNo]
        if line1 != line2:
          invalidCompFound = True
          print("Expected: " + str(line1) + ", Found: " + str(line2))
        reftestLineNo = reftestLineNo + 1

    refFile.close()
    tempFile.close()

    if invalidCompFound:
      print ("UNEXPECTED-TEST-FAIL")
      sys.exit(1)
    else:
      print("TEST-PASS")
      sys.exit(0)

if __name__ == '__main__':
  (patchFile, rootPath) = parseArguments()

  # setup our printing utility vehicle
  gPrinter = PrettyPrinter(COLOR, DEBUG, VERBOSE)

  main(rootPath, patchFile)

  if not patchFile == sys.stdin:
    patchFile.close()

