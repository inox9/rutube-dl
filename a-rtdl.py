import os
import androidhelper
import sys

script = '/storage/emulated/0/pyscripts/rtdl.py'
droid = androidhelper.Android()
args = [script]
url = droid.dialogGetInput(title='URL', message='Enter Rutube.ru URL').result
if not url:
	exit()
args.append(url)
dldir = droid.dialogGetInput(title='Save path', message='Enter save path', defaultText='/storage/emulated/0/Movies').result
if not dldir:
	exit()
args.extend(['-O', dldir])
proxy = droid.dialogGetInput(title='Proxy', message='Use RU proxy? (0/1)', defaultText='0').result
if proxy is None:
	exit()
if proxy == '1':
	args.append('-p')
args.append('-nc')
args.append(os.environ)
os.execle(sys.executable, os.path.basename(sys.executable), *args)
