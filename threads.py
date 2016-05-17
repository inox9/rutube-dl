import threading
import requests
import queue

class DownloadThread(threading.Thread):
	def __init__(self, dq, fn, rq, hdrs):
		threading.Thread.__init__(self)
		self.is_alive = True
		self.dq = dq
		self.fn = fn
		self.rq = rq
		self.hdrs = hdrs

	def run(self):
		fs = open(self.fn, 'wb')
		while True:
			if not self.is_alive:
				fs.close()
				break
			try:
				item = self.dq.get_nowait()
			except queue.Empty:
				fs.close()
				self.rq.put_nowait(None)
				break
			fs.seek(item[1])
			r = requests.get(item[0], stream=True, headers=self.hdrs)
			try:
				for chunk in r.iter_content(chunk_size=8192):
					if chunk:
						fs.write(chunk)
			except Exception:
				fs.close()
				raise
			self.dq.task_done()
			self.rq.put_nowait(int(r.headers['content-length']))

	def kill(self):
		self.is_alive = False

class SizeGetterThread(threading.Thread):
	def __init__(self, cq, rq, hdrs):
		threading.Thread.__init__(self)
		self.is_alive = True
		self.cq = cq
		self.rq = rq
		self.hdrs = hdrs

	def run(self):
		while True:
			if not self.is_alive:
				break
			try:
				item = self.cq.get_nowait()
			except queue.Empty:
				self.rq.put_nowait(None)
				break
			self.rq.put_nowait((item[1], int(requests.head(item[0], headers=self.hdrs).headers['content-length'])))
			self.cq.task_done()

	def kill(self):
		self.is_alive = False
