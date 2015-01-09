#!/usr/bin/env python

from setuptools import setup

entryPoints = {'console_scripts': ['checkiid = checkiid:runMain']}

setup(name='CheckIID',
      version='1.0.4',
      description='Mozilla IID-Checking Script',
      author='Scott Johnson',
      author_email='sjohnson@mozilla.com',
      url='https://github.com/jwir3/checkiid',
      py_modules=['idlutils', 'prettyprinter', 'checkiid'],
      entry_points=entryPoints,
      requires=['argparse', 'difflib']
      )
