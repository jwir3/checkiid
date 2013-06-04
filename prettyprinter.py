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
