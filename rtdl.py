#!/usr/bin/env python3
import sys
if sys.version_info < (3, 1):
	raise Exception('Script requires Python 3.1 or higher to run!')

import requests
import re
import json
import queue
import threading
import subprocess
import os
import random
import tempfile
import html.entities as HE
import multiprocessing
from urllib.parse import urlsplit, urlunsplit
from threads import DownloadThread, SizeGetterThread, ProxyCheckerThread

USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/50.0.2661.75 Safari/537.36'
DOWNLOAD_THREADS = multiprocessing.cpu_count()*2
MB = 1048576 # do not change this

def die(s):
	print(s)
	sys.exit()

def info(s):
	print('[INFO]', s)

def compose_url(base_parsed, newpath):
	new_parsed = list(base_parsed)
	new_parsed[2] = '/'.join((os.path.dirname(base_parsed.path), newpath))
	new_parsed[3] = ''
	return urlunsplit(new_parsed)

def stop_threads(threads):
	for t in threads:
		t.kill()
		t.join()

def proxylist_check(proxies, hdrs):
	chq = queue.Queue()
	resq = queue.Queue()
	for proxy in proxies:
		chq.put(proxy)
	thrs = []
	thr_count = min(DOWNLOAD_THREADS, len(proxies))
	for i in range(thr_count):
		thr = ProxyCheckerThread(chq, resq, hdrs)
		thr.start()
		thrs.append(thr)
	f_thr = 0
	while True:
		try:
			item = resq.get()
			resq.task_done()
		except KeyboardInterrupt: # Ctrl-C is pressed
			info('Stopping proxy checker threads')
			stop_threads(thrs)
			die('Aborted!')
		if item is None:
			f_thr += 1
		else: # we got working proxy from thread, stop another ones
			stop_threads(thrs)
			return item
		if f_thr == thr_count: # all threads have finished proxy checking
			return None

def proxy_get(hdrs):
	sources = (
		('http://hideme.ru/proxy-list/?country=RU&type=hs', r'<td class=tdl>((?:[0-9]{1,3}\.){3}[0-9]{1,3})<\/td><td>(\d+)<\/td>'),
		('http://free-proxy-list.net', r'<tr><td>((?:[0-9]{1,3}\.){3}[0-9]{1,3})<\/td><td>(\d+)<\/td><td>RU<\/td>'),
	)
	for src in sources:
		info('Getting proxies from {}'.format(src[0]))
		proxy_html = requests.get(src[0], headers=hdrs).text
		proxies = re.findall(src[1], proxy_html)
		proxystr = proxylist_check(proxies, hdrs)
		if proxystr is None:
			info('Working proxy was not found')
		else:
			info('Chosen proxy - {}'.format(proxystr))
			return proxystr
	return None

if __name__ == '__main__':
	print('RuTube Downloader v0.4\n')

	if len(sys.argv) < 2:
		die('Usage: rtdl.py rutube_url [-O dir] [-f mkv|mp4] [-p] [-nc]\nCustom params:\n\t-O dir\t\t-- set directory to save result files (default: current working dir)\n\t-f mp4|mkv\t-- set result file format (default: mp4)\n\t-p\t\t-- use RU proxy for downloading country-restricted videos (default: disabled)\n\t-nc\t\t-- don\'t convert source file to MP4/MKV')
	# cli argument parsing
	r = re.match(r'http://rutube\.ru/video/([a-f0-9]+)', sys.argv[1])
	if not r:
		die('Wrong url supplied')

	if '-O' in sys.argv:
		save_dir = sys.argv[sys.argv.index('-O')+1]
		if os.path.exists(save_dir) and not os.path.isdir(save_dir):
			die('Destination path is not a directory')
		if not os.path.exists(save_dir):
			try:
				info('Creating destination directory')
				os.mkdir(save_dir, 0o755)
			except OSError as e:
				die('Cannot create destination dir: {}'.format(e))
	else:
		save_dir = None

	if '-f' in sys.argv:
		oformat = sys.argv[sys.argv.index('-f')+1]
		if oformat not in ('mkv', 'mp4'):
			die('Format should be either mkv or mp4')
	else:
		oformat = 'mp4'

	convert = '-nc' not in sys.argv

	hdrs = {'User-Agent': USER_AGENT, 'Connection': 'keep-alive'}
	if '-p' in sys.argv:
		tmp_file = os.path.join(tempfile.gettempdir(), 'rtdl-lastproxy.txt')
		proxy = None
		if os.path.exists(tmp_file):
			with open(tmp_file, 'r') as fh:
			 	proxy = fh.read(29).rstrip()
			if re.match(r'http:\/\/(?:[0-9]{1,3}\.){3}[0-9]{1,3}:\d{2,5}', proxy):
				try:
					proxied_html = requests.get('http://rutube.ru', proxies={'http': proxy}, timeout=3, headers=hdrs).text
				except Exception:
					proxy = None
				if proxy and 'Rutube' not in proxied_html:
					proxy = None
				
				if proxy:
					info('Chosen previously saved proxy - {}'.format(proxy))
		if not proxy:
			proxy = proxy_get(hdrs)
			if not proxy:
				die('No proxy found, exiting')
			with open(tmp_file, 'w') as fh:
				fh.write(proxy)
		os.environ['HTTP_PROXY'] = proxy

	hdrs['Referer'] = sys.argv[1]
	vhash = r.group(1)
	js = requests.get('http://rutube.ru/api/video/{}'.format(vhash), headers=hdrs).text
	js = json.loads(js)
	title = js['title']
	for ch in ('<', '>', ':', '"', '/', '\\', '|', '?', '*'):
		title = title.replace(ch, '')
	embed_html = requests.get(js['embed_url']).text
	opts = re.search(r'<div id="options" data-value="(.+)"', embed_html)
	if not opts:
		die('No options found')
	opts = opts.group(1)
	for what, to in HE.entitydefs.items():
		opts = opts.replace('&{};'.format(what), to)
	js = json.loads(opts)
	try:
		m3u8 = requests.get(js['video_balancer']['m3u8'], headers=hdrs).text
	except KeyError:
		die('No playlist url found, perhaps video is blocked for this country')
	valid_lines = [x for x in m3u8.splitlines() if x[0] != '#']
	try:
		parts_url = valid_lines[-1] # best quality source available is the last one
	except IndexError:
		die('Cant get main playlist url')
	m3u8 = requests.get(parts_url, headers=hdrs).text
	parsed = urlsplit(parts_url)
	source_fn = os.path.join(save_dir, '{}.ts'.format(title)) if save_dir else '{}.ts'.format(title)
	valid_lines = [x for x in m3u8.splitlines() if x[0] != '#']
	if os.path.exists(source_fn):
		os.remove(source_fn)
	
	info('Saving TS source to: "{}"'.format(source_fn))
	dlq = queue.Queue() # download queue
	resq = queue.Queue() # result queue
	scq  = queue.Queue() # size checker queue
	parts_cnt = len(valid_lines)
	
	if 'HTTP_PROXY' in os.environ: # sources are NOT country-restricted so we can download them without proxy on full speed
		del os.environ['HTTP_PROXY']

	for idx, line in enumerate(valid_lines):
		scq.put((compose_url(parsed, line), idx))
	
	# start threaded getting of content-length
	info('Getting source\'s total size')
	thrs = []
	thr_count = min(DOWNLOAD_THREADS, parts_cnt)
	for i in range(thr_count):
		thr = SizeGetterThread(scq, resq, hdrs)
		thr.start()
		thrs.append(thr)

	f_thr = 0
	sizes = {}
	while True:
		try:
			item = resq.get()
		except KeyboardInterrupt: # Ctrl-C is pressed
			info('Stopping size getter threads')
			stop_threads(thrs)
			die('Aborted!')
		if item is None:
			f_thr += 1
		else:
			sizes[item[0]] = item[1]
			resq.task_done()
		if f_thr == thr_count: # all threads have finished getting content-length
			break
		
	for t in thrs:
		t.join()

	# calculate seek positions for every part
	size_total = 0
	for idx, val in sizes.items():
		dlq.put((compose_url(parsed, valid_lines[idx]), size_total))
		size_total += val
	
	info('Allocating disk space, this may take a while')
	with open(source_fn, 'wb') as fs:
		fs.truncate(size_total)

	resq = queue.Queue()
	# start threaded downloading
	thrs = []
	for i in range(thr_count):
		thr = DownloadThread(dlq, source_fn, resq, hdrs)
		thr.start()
		thrs.append(thr)
	
	parts_dl = 0
	bytes_dl = 0
	f_thr = 0
	mb_size_total = size_total / MB
	while True: # here we process progress messages from threads and actually wait till download finishes
		try:
			item = resq.get()
		except KeyboardInterrupt: # Ctrl-C is pressed
			info('\nStopping download threads')
			stop_threads(thrs)
			info('Removing incompleted source file')
			os.remove(source_fn)
			die('Downloading was aborted!')
		if item is None:
			f_thr += 1
		else:
			bytes_dl += item
			parts_dl += 1
			sys.stdout.write("\r[INFO] Downloading - {0:.1f}%, {3:.1f}/{4:.1f}Mb ({1}/{2})".format(parts_dl / parts_cnt * 100, parts_dl, parts_cnt, bytes_dl / MB, mb_size_total))
			sys.stdout.flush()
			resq.task_done()
		if f_thr == thr_count: # all threads have finished download processing
			break	
	
	for t in thrs:
		t.join()

	print()
	if convert:
		try:
			info('Converting to {} (ffmpeg)'.format(oformat.upper()))
			dest_fn = re.sub(r'ts$', oformat, source_fn)
			subprocess.check_call(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', source_fn, '-c:v', 'copy', '-c:a', 'copy', '-bsf:a', 'aac_adtstoasc', dest_fn])
			info('Removing TS source')
			info('Result file was saved to: "{}"'.format(dest_fn))
			os.remove(source_fn)
		except subprocess.CalledProcessError:
			die('FFMPEG convert ERROR!')
	info('Everything\'s done! Good bye!')
