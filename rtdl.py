#!/usr/bin/env python3
import requests
import re
import sys
import json
import queue
import threading
import lxml.html as LH
import subprocess
import os
import random
from urllib.parse import urlsplit, urlunsplit

USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/50.0.2661.75 Safari/537.36'
DOWNLOAD_THREADS = 4

def die(s):
	print(s)
	sys.exit()

def info(s):
	print('[INFO] {0}'.format(s))

def compose_url(base_parsed, newpath):
	new_parsed = list(base_parsed)
	new_parsed[2] = '/'.join((os.path.dirname(base_parsed.path), newpath))
	new_parsed[3] = ''
	return urlunsplit(new_parsed)

def downloader(dq, fn, rq, hdrs):
	fs = open(fn, 'wb')
	while True:
		try:
			item = dq.get_nowait()
		except queue.Empty:
			fs.close()
			rq.put(None)
			break
		fs.seek(item[1])
		r = requests.get(item[0], stream=True, headers=hdrs)
		try:
			for chunk in r.iter_content(chunk_size=8192):
				if chunk:
					fs.write(chunk)
		except (Exception, KeyboardInterrupt):
			fs.close()
			sys.stdout.write("\n")
			print('Downloading was interrupted!')
			raise
		dq.task_done()
		rq.put_nowait(int(r.headers['content-length']))

def size_checker(cq, rq):
	while True:
		try:
			item = cq.get_nowait()
		except queue.Empty:
			rq.put(None)
			break
		rq.put_nowait((item[1], int(requests.head(item[0]).headers['content-length'])))
		cq.task_done()

MB = 1048576
if __name__ == '__main__':
	print('RuTube Downloader v0.1\n')

	if len(sys.argv) < 2:
		die('Usage: rtdl.py rutube_url [-O dir] [-f mkv|mp4] [-p]\nCustom params:\n\t-O dir\t\t-- set directory to save result files (default: current working dir)\n\t-f mp4|mkv\t-- set result file format (default: mp4)\n\t-p\t\t-- use RU proxy for downloading country-restricted videos (default: disabled)')
	r = re.match(r'http://rutube\.ru/video/([a-f0-9]+)', sys.argv[1])
	if not r:
		die('Wrong url supplied')

	if '-O' in sys.argv:
		save_dir = sys.argv[sys.argv.index('-O')+1]
		if not os.path.exists(save_dir):
			die('Destination dir does not exist')

	if '-f' in sys.argv:
		oformat = sys.argv[sys.argv.index('-f')+1]
		if oformat not in ('mkv', 'mp4'):
			die('Format should be either mkv or mp4')
	else:
		oformat = 'mp4'

	hdrs = {'User-Agent': USER_AGENT, 'Connection': 'keep-alive'}
	if '-p' in sys.argv:
		info('Getting RU proxies list')
		proxy_html = requests.get('http://free-proxy-list.net', headers=hdrs).text
		proxies = LH.fromstring(proxy_html).xpath('//table[@id="proxylisttable"]/tbody/tr/td[position()<4]/text()')
		proxies = list(x for x in zip(*[iter(proxies)] * 3) if x[2] == 'RU')
		random.shuffle(proxies)
		info('Testing proxies')
		for proxy in proxies:
			proxystr = 'http://{0}:{1}'.format(proxy[0], proxy[1])
			try:
				html = requests.get('http://rutube.ru', proxies={'http': proxystr}, timeout=2, headers=hdrs).text
			except Exception:
				continue
			if 'Rutube' not in html:
				continue
			info('Chose proxy -- {0}'.format(proxystr))
			os.environ['HTTP_PROXY'] = proxystr
			break
	hdrs['Referer'] = sys.argv[1]
	vhash = r.group(1)
	js = requests.get('http://rutube.ru/api/video/{0}'.format(vhash), headers=hdrs).text
	js = json.loads(js)
	title = js['title']
	for ch in ('<', '>', ':', '"', '/', '\\', '|', '?', '*'):
		title = title.replace(ch, '')
	html = requests.get(js['embed_url']).text
	opts = LH.fromstring(html).xpath('//div[@id="options"]/@data-value')[0]
	if not opts:
		die('No options found')
	js = json.loads(opts)
	try:
		m3u8 = requests.get(js['video_balancer']['m3u8'], headers=hdrs).text
	except KeyError:
		die('No playlist url found, perhaps video is blocked for this country')
	valid_lines = list(x for x in m3u8.splitlines() if x[0] != '#')
	try:
		parts_url = valid_lines[-1] # best quality source available is the last one
	except IndexError:
		die('Cant get main playlist url')
	m3u8 = requests.get(parts_url, headers=hdrs).text
	parsed = urlsplit(parts_url)
	source_fn = os.path.join(save_dir, '{0}.ts'.format(title)) if save_dir else '{0}.ts'.format(title)
	valid_lines = list(x for x in m3u8.splitlines() if x[0] != '#')
	if os.path.exists(source_fn):
		os.remove(source_fn)
	
	info('Saving TS source to: "{0}"'.format(source_fn))
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
	for i in range(DOWNLOAD_THREADS):
		thr = threading.Thread(target=size_checker, args=(scq, resq))
		thr.start()
		thrs.append(thr)

	f_thr = 0
	sizes = {}
	while True:
		item = resq.get()
		if item is None:
			f_thr += 1
		else:
			sizes[item[0]] = item[1]
			resq.task_done()
		if f_thr == DOWNLOAD_THREADS:
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
	for i in range(DOWNLOAD_THREADS):
		thr = threading.Thread(target=downloader, args=(dlq, source_fn, resq, hdrs))
		thr.start()
		thrs.append(thr)
	
	parts_dl = 0
	bytes_dl = 0
	f_thr = 0
	mb_size_total = round(size_total / MB, 1)
	while True: # here we process progress messages from threads and actually wait till download finishes
		item = resq.get()
		if item is None:
			f_thr += 1
		else:
			bytes_dl += item
			parts_dl += 1
			sys.stdout.write("\r[INFO] Download in progress -- {0}%, {3}/{4}Mb ({1}/{2})".format(round(parts_dl / parts_cnt * 100, 1), parts_dl, parts_cnt, round(bytes_dl / MB, 1), mb_size_total))
			sys.stdout.flush()
			resq.task_done()
		if f_thr == DOWNLOAD_THREADS: # all threads have finished download processing
			break
	
	for t in thrs:
		t.join()

	sys.stdout.write("\n")
	try:
		info('Converting to {0}'.format(oformat.upper()))
		dest_fn = re.sub(r'ts$', oformat, source_fn)
		subprocess.check_call(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', source_fn, '-c:v', 'copy', '-c:a', 'copy', '-bsf:a', 'aac_adtstoasc', dest_fn])
		info('Removing TS source')
		info('Result file was saved to: "{0}"'.format(dest_fn))
		os.remove(source_fn)
	except subprocess.CalledProcessError:
		die('FFMPEG convert ERROR!')
	info('Everything\'s done!')
