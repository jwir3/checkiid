import re

class IDLDescriptor:
  # Static list of IDL descriptors
  kDescriptorList = []

  def __init__(self, aToken, aAffectsBinaryCompat):
    self.mToken = aToken
    self.mBinaryCompat = aAffectsBinaryCompat

  def affectsBinaryCompatibility(self):
    return self.mBinaryCompat

  def getToken(self):
    return self.mToken

  def isInLine (self, aLine, aPrinter=None):
    match = re.search("^(\s)*[\+\-](\s)*\[(.*)](.*)", aLine)
    if match:
      splitAttrs = match.group(3).split(",")
      for attr in splitAttrs:
        if aPrinter:
          aPrinter.debug("Found descriptor: " + attr)
        if attr == self.mToken:
          return True
    return False

  def hasDescriptorsInLine(aLine, aPrinter=None):
    for desc in IDLDescriptor.kDescriptorList:
      if desc.isInLine(aLine, aPrinter):
        return True
    return False

  def areDescriptorsInLineAffectingBinaryCompat(aLine, aPrinter=None):
    if not IDLDescriptor.hasDescriptorsInLine(aLine, aPrinter):
      return False

    for desc in IDLDescriptor.kDescriptorList:
      if desc.affectsBinaryCompatibility:
        if aPrinter:
          aPrinter.debug("Descriptor: " + desc.getToken() + " affects binary compatibility.")
        return True

    if aPrinter:
      aPrinter.debug("No descriptors found affecting binary compatibility.")
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
