import struct
import cv2
# import open3d as o3d
import numpy as np
from numpy import matmul
# from itertools import product
# import pdb
from math import sqrt
from math import isqrt
from . import info_dict as ind
import glob
import time
import os
import csv
from operator import itemgetter
from random import shuffle
from pathlib import Path

# Vendored: template STLs live inside this package (templates/), resolved
# relative to __file__ so the library never depends on CWD. See PROVENANCE.md.
_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

class Vertex:
	def __init__(self, x, y, z):
		self.x = x
		self.y = y
		self.z = z

	def rotate(self, rm):
		pt = [self.x, self.y, self.z]
		mult = matmul(rm, pt)
		self.x = mult[0]
		self.y = mult[1]
		self.z = mult[2]

	def translate(self, tm):
		self.x = self.x + tm[0]
		self.y = self.y + tm[1]
		self.z = self.z + tm[2]

	def get_vertex(self, r=None):
		if r is None:
			return [self.x, self.y, self.z]
		else:
			return [round(self.x, r), round(self.y, r), round(self.z, r)]

	def __str__(self):
		return '({0}, {1}, {2})'.format(self.x, self.y, self.z)

	def __sub__(self, other):
		# return np.array([self.x - other.x, self.y - other.y, self.z - other.z])
		# return Vertex(self.x - other.x, self.y - other.y, self.z - other.z) 
		return [self.x - other.x, self.y - other.y, self.z - other.z]

	# def __eq__(self, other):
	# 	if (self.x == other.x) and (self.y == other.y) and (self.z == other.z):
	# 		return True
	# 	else:
	# 		return False

class Edge:
	def __init__(self, v1, v2):
		self.v1 = v1
		self.v2 = v2
	def __eq__(self, other):
		return ((self.v1 == other.v1) and (self.v2 == other.v2)) or \
				((self.v1 == other.v2) and (self.v2 == other.v1))

class Triangle:
	def __init__(self, *args, **kwargs):
		# nx, ny, nz, v1, v2, v3):
		# nmag = sqrt(nx**2 + ny**2 + nz**2)
		# # nmag = 123
		# # self.normalVector = (nx/nmag, ny/nmag, nz/nmag)
		# self.normalVector = (nx, ny, nz)
		# print('start')
		# print(nx/nmag, ny/nmag, nz/nmag)
		# print(self.normalVector)
		# print('end')
		if len(args) == 2:
			# print('here')
			v1 = args[1]
			v2 = args[0].v1
			v3 = args[0].v2
			
			# this is wrong, fix this later
			nx = 1
			ny = 1
			nz = 1


			self.v1 = v1
			self.v2 = v2
			self.v3 = v3

			self.normalVector = (1, 1, 1)

			self.e1 = Edge(v1, v2)
			self.e2 = args[0]
			self.e3 = Edge(v3, v1)

		else:
			nx = args[0]
			ny = args[1]
			nz = args[2]
			v1 = args[3]
			v2 = args[4]
			v3 = args[5]

			# start MOD0003
			if (nx == 0.0) and (ny == 0.0):
				if nz >= 0:
					self.normalVector = (0.0, 0.0, 1.0)
				else:
					self.normalVector = (0.0, 0.0, -1.0)
			elif (nx == 0.0) and (nz == 0.0):
				if ny >= 0:
					self.normalVector = (0.0, 1.0, 0.0)
				else:
					self.normalVector = (0.0, -1.0, 0.0)
			elif (ny == 0.0) and (nz == 0.0):
				if nx >= 0:
					self.normalVector = (1.0, 0.0, 0.0)
				else:
					self.normalVector = (-1.0, 0.0, 0.0)
			# end MOD0003
			else:
				nmag = sqrt(nx**2 + ny**2 + nz**2)
				self.normalVector = (nx/nmag, ny/nmag, nz/nmag)



			self.v1 = v1
			self.v2 = v2
			self.v3 = v3

			self.e1 = Edge(v1, v2)
			self.e2 = Edge(v2, v3)
			self.e3 = Edge(v3, v1)

	def get_Normals(self):
		return list(self.normalVector)

	def rotate(self, rm):
		# dont use generally, in a mesh rotating with this will rotate 
		# points multiple times since there is no check for if a pt has
		# already been rotated
		self.v1.rotate(rm)
		self.v2.rotate(rm)
		self.v3.rotate(rm)

		# self.normalVector = tuple(matmul(rm, np.array(self.normalVector)))
		self.normalVector = tuple(matmul(rm, self.normalVector))

		self.e1 = Edge(self.v1, self.v2)
		self.e2 = Edge(self.v2, self.v3)
		self.e3 = Edge(self.v3, self.v1)

	def translate(self, tm):
		# dont use generally, in a mesh translating with this will translate 
		# points multiple times since there is no check for if a pt has
		# already been translated
		# print(self.get_vertexList())
		self.v1.translate(tm)
		self.v2.translate(tm)
		self.v3.translate(tm)

		nmag = sqrt((self.normalVector[0]+tm[0])**2 + \
					(self.normalVector[1]+tm[1])**2 +  \
					(self.normalVector[2]+tm[2])**2)
		self.normalVector = ((self.normalVector[0]+tm[0])/nmag, \
								(self.normalVector[1]+tm[1])/nmag, \
								(self.normalVector[2]+tm[2])/nmag)

		self.e1 = Edge(self.v1, self.v2)
		self.e2 = Edge(self.v2, self.v3)
		self.e3 = Edge(self.v3, self.v1)
	
	def flip_normal(self):
		nv = (self.normalVector[0]*-1, self.normalVector[1]*-1, self.normalVector[2]*-1)
		self.normalVector = nv

	def get_vertexList(self):
		return [self.v1.get_vertex(), self.v2.get_vertex(), self.v3.get_vertex()]

	#start MOD0005
	def update_normal(self):
		def dumb(v1, v2):
			x = ((v1[1] * v2[2]) - (v1[2] * v2[1]))
			y = ((v1[2] * v2[0]) - (v1[0] * v2[2]))
			z = ((v1[0] * v2[1]) - (v1[1] * v2[0]))
			return (x, y, z)

		if self.v2.x < self.v3.x:
			n1 = dumb(self.v2 - self.v1, self.v2 - self.v3)
		else:
			n1 = dumb(self.v3 - self.v2, self.v3 - self.v1)

		nx = n1[0]
		ny = n1[1]
		nz = n1[2]

		if (nx == 0.0) and (ny == 0.0):
			if nz >= 0:
				self.normalVector = (0.0, 0.0, 1.0)
			else:
				self.normalVector = (0.0, 0.0, -1.0)
		elif (nx == 0.0) and (nz == 0.0):
			if ny >= 0:
				self.normalVector = (0.0, 1.0, 0.0)
			else:
				self.normalVector = (0.0, -1.0, 0.0)
		elif (ny == 0.0) and (nz == 0.0):
			if nx >= 0:
				self.normalVector = (1.0, 0.0, 0.0)
			else:
				self.normalVector = (-1.0, 0.0, 0.0)
		else:
			nmag = sqrt(nx**2 + ny**2 + nz**2)
			self.normalVector = (nx/nmag, ny/nmag, nz/nmag)
		# print(self.normalVector)
	#end MOD0005



	def __str__(self):
		return "{{{0}, {1}, {2}, {3}}}".format(str(self.normalVector), \
											   str(self.v1), \
											   str(self.v2), \
											   str(self.v3))
	def __eq__(self, other):
		return (self.v1 == other.v1) and \
				(self.v2 == other.v2) and \
				(self.v3 == other.v3) and \
				(self.normalVector == other.normalVector) and \
				(self.e1 == other.e1) and \
				(self.e2 == other.e2) and \
				(self.e3 == other.e3)

class Mesh:
	def __init__(self):
		self.triangles = []
		self.vertexList = set()
		self.normalsList = []

	def add_Triangle(self, tri):
		self.triangles.append(tri)
		# print(len(self.triangles))
		self.vertexList.add(tri.v1)
		self.vertexList.add(tri.v2)
		self.vertexList.add(tri.v3)
		self.normalsList.append(tri.normalVector)

	def remove_Triangle(self, tri):
		self.triangles.remove(tri)

	def rotate(self, rm):
		rt1 = time.perf_counter()
		if rm is None:
			pass
		else:
			for i in self.vertexList:
				i.rotate(rm)
			for j in self.triangles:
				# start MOD0006
				j.update_normal()
				# j.normalVector = tuple(matmul(rm, j.normalVector))
				# end MOD0006

		rt2 = time.perf_counter()
		print('rotate {}'.format(rt2-rt1))
		# if rm is None:
		# 	pass
		# else:
		# 	for j in self.triangles:
		# 		j.rotate(rm)
		# 	self.update_VertexList()

	def translate(self, tm):
		tt1 = time.perf_counter()
		if tm is None:
			pass
		else:
			for i in self.vertexList:
				i.translate(tm)
			for j in self.triangles:
				j.normalVector = (j.normalVector[0]+tm[0], \
									j.normalVector[1]+tm[1], \
									j.normalVector[2]+tm[2])
		tt2 = time.perf_counter()
		print('translate {}'.format(tt2-tt1))
		# for j in self.triangles:
		# 	j.translate(tm)
		# self.update_VertexList()

	def flip_normals(self):
		for tri in self.triangles:
			tri.flip_normal()

	def update_VertexList(self):
		# very inefficient on large vertex sets, due to nature of sets
		vl = set()
		for i in self.triangles:
			vl.add(i.v1)
			vl.add(i.v2)
			vl.add(i.v3)
		self.vertexList = vl

	def get_pointList(self):
		s = set()
		l = []
		for i in self.triangles:
			vl = i.get_vertexList()
			s.add(tuple(vl[0]))
			s.add(tuple(vl[1]))
			s.add(tuple(vl[2]))
		for j in s:
			l.append(list(j))

		return np.array(l)

	def print_stats(self, n):
		print('     Name: ' + n)
		print('Triangles: ' + str(len(self.triangles)))
		print('   Points: ' + str(len(self.vertexList)))

	def __len__(self):
		return len(self.triangles)

	def __str__(self):
		ret = ''
		for i in self.triangles:
			ret = ret + "  " + str(i) + "\n"
		return "{{\n{0}}}".format(ret)

	def __add__(self, other):
		self.triangles += other.triangles
		self.vertexList = self.vertexList.union(other.vertexList)
		self.normalsList += other.normalsList
		return self




def open_stl_binary(fn):
	# Only for binary STL files
	fileContents = open(fn, mode='rb').read()
	header = fileContents[:80].decode('utf-8')
	# print(header)
	triangles = fileContents[80:84]
	triangles = int.from_bytes(triangles, 'little')
	# print(triangles)

	mesh = Mesh()

	for i in range(84,len(fileContents), 50):
		# print(struct.unpack('<f', fileContents[i+12:i+16])[0])
		mesh.add_Triangle(Triangle(struct.unpack('<f', fileContents[i:i+4])[0], \
									struct.unpack('<f', fileContents[i+4:i+8])[0], \
									struct.unpack('<f', fileContents[i+8:i+12])[0], \
									Vertex(struct.unpack('<f', fileContents[i+12:i+16])[0], \
											struct.unpack('<f', fileContents[i+16:i+20])[0], \
											struct.unpack('<f', fileContents[i+20:i+24])[0]), \
									Vertex(struct.unpack('<f', fileContents[i+24:i+28])[0], \
											struct.unpack('<f', fileContents[i+28:i+32])[0], \
											struct.unpack('<f', fileContents[i+32:i+36])[0]), \
									Vertex(struct.unpack('<f', fileContents[i+36:i+40])[0], \
											struct.unpack('<f', fileContents[i+40:i+44])[0], \
											struct.unpack('<f', fileContents[i+44:i+48])[0])))

	return mesh

def importImg(path, gauss=(5,5), thresh=False, mirror=True, invert=True, corrected=False, mimg=None):
	if not corrected:
		img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
		# cv2.imshow('new', img)
		# cv2.waitKey(0)
		# if img.depth() == 16:

	# start MOD0001
		print(img.shape)
		print(img.dtype)
		if len(img.shape) > 2:
			if img.shape[2] == 4:
				# print('start')
				# print('img shape[2] == 4')
				# print(img[0,0])
				trans_mask = img[:,:,3] == 0
				# img[trans_mask] = [255, 255, 255, 255]
				img[trans_mask] = [0, 0, 0, 0]
				img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
				# print(img[0,0])
				# print('end')

			gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)	
		else:
			gray = img

		if gray.dtype == 'uint16':
			gray2 = (gray/256.0).astype('uint8') 
			# gray = cv2.normalize(gray, gray, 0, 255, cv2.NORM_MINMAX)
			gray = gray2
			print('shape')
			print(gray.shape)
			print(gray.dtype)
			# gray = img2
		# cv2.imshow('new', gray)
		# cv2.waitKey(0)
	# end MOD0001
		
		# print(SQUARE_COLOR)
		if mirror:
			gray = cv2.flip(gray, 1)
		#MOD0007 start
		SQUARE_COLOR = 255 if (int(gray[0,0])/2) > 127 else 0
		if invert:
			gray = cv2.bitwise_not(gray)
		# 	SQUARE_COLOR = 255 if (int(gray[0,0])/2) > 127 else 0
		# else:
		# 	SQUARE_COLOR = 255 if (int(gray[0,0])/2) > 127 else 0
		#MOD0007 end
		# print('sqrclr'+str(SQUARE_COLOR))
		BORDER_COLOR = 255
		# print('color')
		# print(type(gray[0,0]))
		gray = cv2.GaussianBlur(gray, gauss,0)

		keepCropping = True
		while keepCropping:
			print(gray.shape)
			# print(gray[0])
			# print(len(gray[0]))
			# print(gray[:][0])
			# print(len(gray[:][0]))
			# print(len(gray[0]))
			yzmi = min(gray[0])
			yzma = max(gray[0])
			ymmi = min(gray[gray.shape[0]-1])
			ymma = max(gray[gray.shape[0]-1])
			# xzmi = min(gray[:][0])
			# xzma = max(gray[:][0])
			# xmmi = min(gray[:][gray.shape[1]-1])
			# xmma = max(gray[:][gray.shape[1]-1])
			xzmi = min(gray[:,0])
			xzma = max(gray[:,0])
			xmmi = min(gray[:,gray.shape[1]-1])
			xmma = max(gray[:,gray.shape[1]-1])
			print(yzmi)
			print(yzma)
			print(ymmi)
			print(ymma)
			print(xzmi)
			print(xzma)
			print(xmmi)
			print(xmma)
			print(SQUARE_COLOR)

			yzero = (yzmi == yzma) and (yzmi == SQUARE_COLOR)
			ymax = (ymmi == ymma) and (ymmi == SQUARE_COLOR)
			xzero = (xzmi == xzma) and (xzmi == SQUARE_COLOR)
			xmax = (xmmi == xmma) and (xmmi == SQUARE_COLOR)

			if yzero and ymax and xzero and xmax:
				print('here')
				crop = gray[1:gray.shape[0]-1, 1:gray.shape[1]-1]
				gray = crop
			else:
				keepCropping = False
		# cv2.imshow('new', gray)
		# cv2.waitKey(0)

		iwidth = gray.shape[1]
		iheight = gray.shape[0]
		desiredSize = 500
		if (iwidth > desiredSize) or (iheight > desiredSize):
			scaleFactor = desiredSize/(max(iwidth, iheight))
			nx = iwidth*scaleFactor
			ny = iheight*scaleFactor
			gray = cv2.resize(gray, (int(nx),int(ny)), interpolation=cv2.INTER_AREA)
			print(gray.shape)

		# gray = cv2.GaussianBlur(gray, gauss,0)

		if thresh:
			# gray = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 25, 2)
			_,gray = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)
		# print(a)
		# print(np.unique(gray, return_index=False, return_inverse=False, return_counts=False, axis=None))

		

		# x,y,w,h = cv2.boundingRect(gray)
		# x, y, w, h = 
		# print(x, y, w, h)
		# margin = [10, 10]
		# gray = gray[y-margin[0]:y+h+margin[0], x-margin[0]:x+w+margin[0]].copy()
		# cv2.imshow('new', gray)
		# cv2.waitKey(0)

		# print(np.unique(gray, return_index=False, return_inverse=False, return_counts=False, axis=None))
		if gray.shape[0] > gray.shape[1]:
			s1 = (gray.shape[0] - gray.shape[1])//2
			# img2 = cv2.copyMakeBorder(gray, 0, 0, s1, s1, cv2.BORDER_CONSTANT, BORDER_COLOR)
			img2 = cv2.copyMakeBorder(gray, top=0, bottom=0, left=s1, right=s1, borderType=cv2.BORDER_CONSTANT, \
												value=SQUARE_COLOR)
		elif gray.shape[0] < gray.shape[1]:
			s1 = (gray.shape[1] - gray.shape[0])//2
			# img2 = cv2.copyMakeBorder(gray, s1, s1, 0, 0, cv2.BORDER_CONSTANT, BORDER_COLOR)
			img2 = cv2.copyMakeBorder(gray, top=s1, bottom=s1, left=0, right=0, borderType=cv2.BORDER_CONSTANT, \
												value=SQUARE_COLOR)
		else:
			img2 = gray

		

		# cv2.imshow('new', img2)
		# cv2.waitKey(0)
		border = 10
		img2 = cv2.copyMakeBorder(img2, top=border, bottom=border, left=border, right=border, \
									borderType=cv2.BORDER_CONSTANT, value=BORDER_COLOR)

		# cv2.imshow('new', img2)
		# cv2.waitKey(0)
		print(img2.shape)
		return img2
		
	else:
		gray = mimg
		# gray = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)
		if mirror:
			gray = cv2.flip(gray, 1)
		gray = cv2.bitwise_not(gray)
		return gray

def crossProd(v1, v2):
	# x = ((v1.y * v2.z) - (v1.z * v2.y))
	# y = ((v1.z * v2.x) - (v1.x * v2.z))
	# z = ((v1.x * v2.y) - (v1.y * v2.x))
	x = ((v1[1] * v2[2]) - (v1[2] * v2[1]))
	y = ((v1[2] * v2[0]) - (v1[0] * v2[2]))
	z = ((v1[0] * v2[1]) - (v1[1] * v2[0]))
	return (x, y, z)

def img2Mesh(i, depth=1, xwidth=5, ywidth=5, yz_swap=False):
	m = Mesh()
	vlist = []
	# print(i.shape)
	ot1 = time.perf_counter()
	

	for y in range(0, i.shape[0]):
		vlist.append([])
		for x in range(0, i.shape[1]):
			if yz_swap:
				v = Vertex((x*xwidth)/(i.shape[1]-1), ((i[y, x]*depth)/255), (y*ywidth)/(i.shape[0]-1))
				# print(y/i.shape[0])
			else:
				v = Vertex((x*xwidth)/(i.shape[1]-1), (y*ywidth)/(i.shape[0]-1), ((i[y, x]*depth)/255))
			vlist[y].append(v)
	# print(len(vlist))
	# print(len(vlist[0]))
	ot2 = time.perf_counter()
	for y in range(1, i.shape[0]):
		for x in range(1, i.shape[1]):
			# time3 = time.perf_counter()
			v1 = vlist[y-1][x-1]
			v2 = vlist[y][x-1]
			v3 = vlist[y-1][x]
			v4 = vlist[y][x]
			# time4 = time.perf_counter()# - time3

			# n1 = cross(v4 - v1, v2 - v1)
			# n2 = cross(v4 - v1, v3 - v1)


			# start MOD0002
			# n1 = crossProd(v4 - v1, v2 - v1)
			# n2 = crossProd(v4 - v1, v1 - v3)
			n1 = crossProd(v2 - v1, v2 - v4)
			n2 = crossProd(v3 - v4, v3 - v1)
			
			# time5 = time.perf_counter()# - time4

			t1 = Triangle(n1[0], n1[1], n1[2], v1, v2, v4)
			t2 = Triangle(n2[0], n2[1], n2[2], v1, v4, v3)
			# end MOD0002


			# time6 = time.perf_counter()# - time5

			m.add_Triangle(t1)
			m.add_Triangle(t2)
			# time7 = time.perf_counter()# - time6
			# time8 = time.perf_counter() - time3
			# print('{},{}'.format(y, x))
			# print('array locate {}'.format(time4-time3))
			# print('cross {}'.format(time5-time4))
			# print('triangles {}'.format(time6-time5))
			# print('add tris {}'.format(time7-time6))
			# print('overall {}'.format(time8))
	ot3 = time.perf_counter()
	print('img2mesh')
	print('first loop {}'.format(ot2-ot1))
	print('second loop {}'.format(ot3-ot2))
	print('overall {}'.format(ot3-ot1))


	return m

def circumcircle(v, t):
	ax = t.v1.x
	ay = t.v1.y
	bx = t.v2.x
	by = t.v2.y
	cx = t.v3.x
	cy = t.v3.y
	vx = v.x
	vy = v.y

	# D = 2*((ax)*(by-cy) + (bx)*(cy-ay) + (cx)*(ay-by))
	# ux = (1/D)*((ax**2 + ay**2)*(by-cy) + (bx**2 + by**2)*(cy-ay) + (cx**2 + cy**2)*(ay-by))
	# uy = (1/D)*((ax**2 + ay**2)*(cx-bx) + (bx**2 + by**2)*(ax-cx) + (cx**2 + cy**2)*(bx-ax))

	# vdist = sqrt((vx-ux)**2 + (vy-uy)**2)
	# adist = sqrt((ax-ux)**2 + (ay-uy)**2)

	# return vdist < adist

	ccw = ((bx - ax)*(cy - ay)-(cx - ax)*(by - ay) > 0)

	ax_ = ax-vx
	ay_ = ay-vy
	bx_ = bx-vx
	by_ = by-vy
	cx_ = cx-vx
	cy_ = cy-vy

	r1 = (ax_*ax_ + ay_*ay_) * (bx_*cy_ - cx_*by_) - \
		(bx_*bx_ + by_*by_) * (ax_*cy_ - cx_*ay_) + \
		(cx_*cx_ + cy_*cy_) * (ax_*by_ - bx_*ay_)

	# print(r1)

	if ccw:
		return r1 > 0
	else:
		return r1 < 0



def new_edge(e, t):
	elist = []
	for t1 in t:
		elist.append(t1.e1)
		elist.append(t1.e2)
		elist.append(t1.e3)
	# print(elist)
	# print(e)
	# return not (e in elist)
	return (elist.count(e) == 1)


def img2Mesh2(i, depth=1, xwidth=5, ywidth=5, yz_swap=False):
	xwidth = 25
	ywidth = 25



	m = Mesh()
	vlist = []
	vlist2 = []

	# print(i.shape)
	ot1 = time.perf_counter()
	

	for y in range(0, i.shape[0]):
		vlist.append([])
		for x in range(0, i.shape[1]):
			try:
				addv = False
				# on a corner, add value
				if (((y == 0) and (x == 0)) or ((y == 0) and (x == i.shape[1]-1)) 
					or ((y == i.shape[0]-1) and (x == 0)) or ((y == i.shape[0]-1) and (x == i.shape[1]-1))):
					vlist[y].append(i[y][x])
					addv = True
				# on top row
				elif (y == 0):
					p1 = i[y][x]
					p2 = i[y][x-1]
					p3 = i[y][x+1]
					p4 = i[y+1][x]
					p5 = i[y+1][x-1]
					p6 = i[y+1][x+1]
					if p1 == p2 == p3 == p4 == p5 == p6:
						vlist[y].append(None)
					else:
						vlist[y].append(p1)
						# vlist2.append((sqrt(y**2+x**2), x, y, p1))
						addv = True
				elif (y == i.shape[0]-1):
					p1 = i[y][x]
					p2 = i[y][x-1]
					p3 = i[y][x+1]
					p4 = i[y-1][x]
					p5 = i[y-1][x-1]
					p6 = i[y-1][x+1]
					if p1 == p2 == p3 == p4 == p5 == p6:
						vlist[y].append(None)
					else:
						vlist[y].append(p1)
						# vlist2.append((sqrt(y**2+x**2), x, y, p1))
						addv = True
				elif (x == 0):
					p1 = i[y][x]
					p2 = i[y-1][x]
					p3 = i[y+1][x]
					p4 = i[y][x+1]
					p5 = i[y-1][x+1]
					p6 = i[y+1][x+1]
					if p1 == p2 == p3 == p4 == p5 == p6:
						vlist[y].append(None)
					else:
						vlist[y].append(p1)
						# vlist2.append((sqrt(y**2+x**2), x, y, p1))
						addv = True
				elif (x == i.shape[1]-1):
					p1 = i[y][x]
					p2 = i[y-1][x]
					p3 = i[y+1][x]
					p4 = i[y][x-1]
					p5 = i[y-1][x-1]
					p6 = i[y+1][x-1]
					if p1 == p2 == p3 == p4 == p5 == p6:
						vlist[y].append(None)
					else:
						vlist[y].append(p1)
						# vlist2.append((sqrt(y**2+x**2), x, y, p1))
						addv = True
				else:
					p1 = i[y][x]
					p2 = i[y][x-1]
					p3 = i[y][x+1]
					p4 = i[y-1][x]
					p5 = i[y-1][x-1]
					p6 = i[y-1][x+1]
					p7 = i[y+1][x]
					p8 = i[y+1][x-1]
					p9 = i[y+1][x+1]
					if p1 == p2 == p3 == p4 == p5 == p6 == p7 == p8 == p9:
						vlist[y].append(None)
					else:
						vlist[y].append(p1)
						# vlist2.append((sqrt(y**2+x**2), x, y, p1))
						addv = True
				if addv:
					if yz_swap:
						vlist2.append(Vertex((x*xwidth)/(i.shape[1]-1), ((i[y, x]*depth)/255), (y*ywidth)/(i.shape[0]-1)))
						# print(y/i.shape[0])
					else:
						vlist2.append(Vertex((x*xwidth)/(i.shape[1]-1), (y*ywidth)/(i.shape[0]-1), ((i[y, x]*depth)/255)))

			except:
				print('error')
				print(y)
				print(x)
				print(i.shape)
					
	# shuffle(vlist2)
	m2 = Mesh()
	if yz_swap:
		supertri_v1 = Vertex(-1000, -1000, -1000)
		supertri_v2 = Vertex(1000, -1000, 1000)
		supertri_v3 = Vertex(2000, 2000, 2000)
	else:
		supertri_v1 = Vertex(-1000, -1000, 0)
		supertri_v2 = Vertex(1000, -1000, 0)
		supertri_v3 = Vertex(-1000, 2000, 0)
	supertri_v_list = [supertri_v1, supertri_v2, supertri_v3]
	m2.add_Triangle(Triangle(1, 0, 0, supertri_v1, supertri_v2, supertri_v3))
	
	print(len(vlist2))
	
	
	valcounter = 0
	for vert in vlist2:
		len0 = time.perf_counter()
		valcounter += 1
		bad_tris = []
		# print(m2)
		for tri in m2.triangles:
			len1 = time.perf_counter()
			if circumcircle(vert, tri):
				# print('made it here')
				bad_tris.append(tri)
				# print(bad_tris)
			len2 = time.perf_counter()

		polygon = []
		for tri in bad_tris:
			for edge in [tri.e1, tri.e2, tri.e3]:
				len3 = time.perf_counter()
				if new_edge(edge, bad_tris):
					# print('made it here 2')
					polygon.append(edge)
				len4 = time.perf_counter()
		# print(polygon)
		for tri in bad_tris:
			len5 = time.perf_counter()
			m2.remove_Triangle(tri)
			len6 = time.perf_counter()
		for edge in polygon:
			len7 = time.perf_counter()
			m2.add_Triangle(Triangle(edge, vert))
			len8 = time.perf_counter()
		if (valcounter % 200) == 0 :
			print(valcounter)
			print('   circumcircle: {}'.format(len2-len1))
			print('       new_edge: {}'.format(len4-len3))
			print('remove_Triangle: {}'.format(len6-len5))
			print('   add_Triangle: {}'.format(len8-len7))
			print('          Total: {}'.format(len8-len0))
			print('      Mesh tris: {}'.format(len(m2.triangles)))
			print('       bad_tris: {}'.format(len(bad_tris)))
		
	print(len(m2.triangles))
	remove_list = []
	for tri in m2.triangles:
		if (tri.v1 in supertri_v_list) or (tri.v2 in supertri_v_list) or (tri.v3 in supertri_v_list):
			remove_list.append(tri)
			# m2.remove_Triangle(tri)
	for tri in remove_list:
		m2.remove_Triangle(tri)




			# vlist[y].append(i[y][x])
	


	# print(len(vlist))
	# print(len(vlist[0]))
	# print(i.shape[0])
	# print(i.shape[1])

			
	# ot2 = time.perf_counter()
	


			

	ot3 = time.perf_counter()
	# print('first loop {}'.format(ot2-ot1))
	# print('second loop {}'.format(ot3-ot2))
	print('overall {}'.format(ot3-ot1))
	# print(vlist)
	# with open("out.csv", "w", newline="") as f:
	# 	writer = csv.writer(f)
	# 	writer.writerows(vlist)
	save_stl(m2, quotePath, moldName+'_F'+'.stl')


	return m2
			
def save_stl(m, p, n):
	if not os.path.exists(p):
		os.makedirs(p)

	lines = [struct.pack("80sI", b'TEST WRITER', len(m))]

	for tri in m.triangles:
		o = []
		norms = tri.get_Normals()
		# print(norms)
		vlist = tri.get_vertexList()
		# print(vlist)
		vlist.insert(0, norms)
		# print(vlist)
		for i in vlist:
			for j in i:
				o.append(j)
		o.append(0)
		lines.append(struct.pack("12fH", *o))
	lines = b"".join(lines)
	# os.chmod(p, 0o777)
	f = open(p+n, 'wb')
	f.write(lines)
	f.close()

def remove_triangles(m, pt_list, rounding=None):
	l = []
	square = []
	for i in m.triangles:
		
		if (i.v1.get_vertex(r=rounding) in pt_list) and (i.v2.get_vertex(r=rounding) in pt_list) and (i.v3.get_vertex(r=rounding) in pt_list):
			# print(i)
			l.append(i)
			# print(i.v1.get_vertex())
			# print(i.v2.get_vertex())
			# print(i.v3.get_vertex())
			if i.v1.get_vertex() not in square:
				square.append(i.v1.get_vertex())
			if i.v2.get_vertex() not in square:
				square.append(i.v2.get_vertex())
			if i.v3.get_vertex() not in square:
				square.append(i.v3.get_vertex())
			# print(i)
	for i in l:
		m.remove_Triangle(i)

	# print(square)
	return square

def create_models(img, product, invert=True):
	# print(ind.info)
	mold_mesh = open_stl_binary(str(_TEMPLATE_DIR / ind.info[product]["Mold"]['location']))
	product_mesh = open_stl_binary(str(_TEMPLATE_DIR / ind.info[product]["Product"]['location']))

	mold_actual_sq = remove_triangles(mold_mesh, ind.info[product]["Mold"]["removeTris"], 4)
	product_actual_sq = remove_triangles(product_mesh, ind.info[product]["Product"]["removeTris"])

	minv = invert
	pinv = invert

	mold_img = importImg(img, \
						ind.info[product]["Image"]["gauss"], \
						thresh=False, \
						mirror=ind.info[product]["Mold"]["mirror"], \
						invert=minv, \
						corrected=ind.info[product]["Mold"]["corrected"])
	product_img = importImg(img, \
						ind.info[product]["Image"]["gauss"], \
						thresh=False, \
						mirror=ind.info[product]["Product"]["mirror"], \
						invert=pinv, \
						corrected=ind.info[product]["Product"]["corrected"], \
						mimg=mold_img)

	# get widths here
	# This should be generalized into a function later which can support any # of triangles
	# currently this will only work under the assumption we have two triangles
	# which create a square plane (4 pts)

	m_min_x = min(mold_actual_sq[0][0], mold_actual_sq[1][0], mold_actual_sq[2][0], mold_actual_sq[3][0])
	m_max_x = max(mold_actual_sq[0][0], mold_actual_sq[1][0], mold_actual_sq[2][0], mold_actual_sq[3][0])
	m_min_y = min(mold_actual_sq[0][1], mold_actual_sq[1][1], mold_actual_sq[2][1], mold_actual_sq[3][1])
	m_max_y = max(mold_actual_sq[0][1], mold_actual_sq[1][1], mold_actual_sq[2][1], mold_actual_sq[3][1])
	m_min_z = min(mold_actual_sq[0][2], mold_actual_sq[1][2], mold_actual_sq[2][2], mold_actual_sq[3][2])
	m_max_z = max(mold_actual_sq[0][2], mold_actual_sq[1][2], mold_actual_sq[2][2], mold_actual_sq[3][2])

	m_xw = abs(m_min_x) + abs(m_max_x)
	m_yw = abs(m_min_y) + abs(m_max_y)
	m_zw = abs(m_min_z) + abs(m_max_z)

	p_min_x = min(product_actual_sq[0][0], product_actual_sq[1][0], product_actual_sq[2][0], product_actual_sq[3][0])
	p_max_x = max(product_actual_sq[0][0], product_actual_sq[1][0], product_actual_sq[2][0], product_actual_sq[3][0])
	p_min_y = min(product_actual_sq[0][1], product_actual_sq[1][1], product_actual_sq[2][1], product_actual_sq[3][1])
	p_max_y = max(product_actual_sq[0][1], product_actual_sq[1][1], product_actual_sq[2][1], product_actual_sq[3][1])
	p_min_z = min(product_actual_sq[0][2], product_actual_sq[1][2], product_actual_sq[2][2], product_actual_sq[3][2])
	p_max_z = max(product_actual_sq[0][2], product_actual_sq[1][2], product_actual_sq[2][2], product_actual_sq[3][2])

	p_xw = abs(p_min_x) + abs(p_max_x)
	p_yw = abs(p_min_y) + abs(p_max_y)
	p_zw = abs(p_min_z) + abs(p_max_z)
	# print([m_min_x, m_min_y, m_min_z])
	# print('mold')
	# print([p_min_x, p_max_y-ind.info[product]["Product"]["depth"], p_min_z])
	# print('prod')
	# print(minv)
	# print(pinv)

	if ind.info[product]["Mold"]["yz_swap"]:
		mold_img_mesh = img2Mesh(mold_img, \
								depth=ind.info[product]["Mold"]["depth"], \
								xwidth=m_xw, \
								ywidth=m_zw, \
								yz_swap=True)
	else:
		mold_img_mesh = img2Mesh(mold_img, \
								depth=ind.info[product]["Mold"]["depth"], \
								xwidth=m_xw, \
								ywidth=m_yw, \
								yz_swap=False)
	
	if minv and not (ind.info[product]["Mold"]["trans_array"] is None):
		mold_img_mesh.translate([ind.info[product]["Mold"]["trans_array"][0], \
								ind.info[product]["Mold"]["trans_array"][1]-ind.info[product]["Mold"]["depth"], \
								ind.info[product]["Mold"]["trans_array"][2]])
		# mold_img_mesh.translate(ind.info[product]["Mold"]["trans_array"])
	elif not (ind.info[product]["Mold"]["trans_array"] is None):
		# mold_img_mesh.translate(ind.info[product]["Mold"]["trans_array"])
		mold_img_mesh.translate([ind.info[product]["Mold"]["trans_array"][0], \
								ind.info[product]["Mold"]["trans_array"][1]-ind.info[product]["Mold"]["depth"], \
								ind.info[product]["Mold"]["trans_array"][2]])
	
	mold_img_mesh.rotate(ind.info[product]["Mold"]["rot_array"])
		
	if ind.info[product]["Product"]["yz_swap"]:
		product_img_mesh = img2Mesh(product_img, \
									depth=ind.info[product]["Product"]["depth"], \
									xwidth=p_xw, \
									ywidth=p_zw, \
									yz_swap=True)
	else:
		product_img_mesh = img2Mesh(product_img, \
									depth=ind.info[product]["Product"]["depth"], \
									xwidth=p_xw, \
									ywidth=p_yw, \
									yz_swap=False)
	
	if pinv:
		product_img_mesh.translate(ind.info[product]["Product"]["trans_array"])
		# product_img_mesh.translate([ind.info[product]["Product"]["trans_array"][0], ind.info[product]["Product"]["trans_array"][1]+ind.info[product]["Product"]["depth"], ind.info[product]["Product"]["trans_array"][2]])
	else:
		# product_img_mesh.translate([ind.info[product]["Product"]["trans_array"][0], ind.info[product]["Product"]["trans_array"][1]+ind.info[product]["Product"]["depth"], ind.info[product]["Product"]["trans_array"][2]])
		product_img_mesh.translate(ind.info[product]["Product"]["trans_array"])

	product_img_mesh.rotate(ind.info[product]["Product"]["rot_array"])



	if ind.info[product]["Mold"]["flip_norms"]:
		mold_img_mesh.flip_normals()
	if ind.info[product]["Product"]["flip_norms"]:
		product_img_mesh.flip_normals()

	mold_final_mesh = mold_mesh + mold_img_mesh
	product_final_mesh = product_mesh + product_img_mesh

	return mold_final_mesh, product_final_mesh


#modifications to bring to new release:
#done - MOD0001 - handle 16 bit images/images without channel data due to opencv not returning channels for proper grayscale
#done - MOD0002 - fix normalization
#done - MOD0003 - fix quick normalization in triangles, account for possibility of being negative
# no longer used#MOD0004 - info_dict, change flip norms to true for 'mold'
#done - MOD0005 - added update normals function (hacky, fix later)
#done - MOD0006 - used update normal function in rotate function
#done - MOD0007 - added SQARE_COLOR logic
