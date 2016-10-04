#!/usr/bin/env python

# started 2009-03-10
# 2009-03-14
# license: WTFPLv2

# TODO handle more exceptions
# TODO support thumbnails smaller than 128x128
# TODO verify the source URI is the same as the one in the thumbnail (for now the MD5 is trusted on)
# TODO .thumblocal
# TODO allow mtime no-check


'''Generate and retrieve thumbnails according to the `Freedesktop.org thumbnail standard`_.

Summary of the thumbnail standard
=================================
* Thumbnails of any file or URL (should it be an image, a video, a webpage) can be stored in ~/.cache/thumbnails in PNG format
* Two sizes are used for thumbnails : 128x128 and 256x256, stored respectively in ~/.cache/thumbnails/normal and ~/.cache/thumbnails/large
* files or URLs thumbnailed must have a modification time (mtime for short) to detect obsolescence of thumbnails
* additional metadata can be put in thumbnails, as key/value pairs, in the PNG text fields, such as the time length for a video file
* there are two attributes required to be put in the metadata : the source URI and the mtime
* if a thumbnail can't be generated by an app (e.g. because of an erroneus file), a "fail-file" can be written in ~/.cache/thumbnails/fail/appname-version

Module functions
================
The module's functions take care of putting the two mandatory attributes in the thumbnail file.

Functions for querying, that do not generate thumbnails, and can be used with files or URLs, that can be non-images :
* thumbnail_path
* existing_thumbnail_path
* is_thumbnail_failed

Functions that have side effects, which write thumbnails, or "fail-files", they can require local-files (see the function's doc) :
* gen_image_thumbnail
* force_gen_image_thumbnail
* put_thumbnail
* put_fail


Examples
========

Just ask for thumbnails of local images, automatically creating them if necessary::
  thumb_image = gen_image_thumbnail('/my/file.jpg')
  local_app_display(thumb_image)

Ask for a thumbnail or generate it manually, for example a web-browser generating pages previews, that this module can't do himself::
  orig_url = 'http://example.com/file.pdf'
  thumb_image = existing_thumbnail_path(orig_url, mtime=0) # mtime is not used in this example

  if not thumb_image:
    try:
      local_app_make_preview(orig_url, '/tmp/preview.jpg')
    except NetworkError:
      put_fail(orig_url, 'mybrowser-1.0', mtime=0)
    else:
      thumb_image = put_thumbnail(orig_url, '/tmp/preview.jpg', mtime=0)
    if is_thumbnail_failed(orig_url):
      thumb_image = 'error.png'

  local_app_display(thumb_image)


.. _Freedesktop.org thumbnail standard: http://triq.net/~jens/thumbnail-spec/index.html

'''


import PIL.Image as PILI
import PIL.PngImagePlugin as PILP
import md5
import os
import re
import tempfile

__all__ = 'thumbnail_path existing_thumbnail_path is_thumbnail_failed gen_image_thumbnail force_gen_image_thumbnail put_thumbnail put_fail'.split()


def _any2size(size):
	if size in ('large', 256, '256'):
		return (256, 'large')
	elif size in ('normal', 128, '128'):
		return (128, 'normal')
	else:
		try:
			if 0 < int(size) <= 128:
				return (128, 'normal')
		except ValueError:
			pass

_URI_RE = re.compile(r'[a-zA-Z0-9.+-]+:')

def _any2uri(sth):
	'''Get an URI from the parameter

	If it's already an URI, return it, else return a file:// URL of it
	'''

	if _URI_RE.match(sth):
		return sth
	else:
		return 'file://' + os.path.abspath(sth)

def _create_pnginfo(uri, mtime, moreinfo=None):
	outinfo = PILP.PngInfo()

	outinfo.add_text('Thumb::URI', uri)
	outinfo.add_text('Thumb::MTime', str(mtime))

	if moreinfo:
		for k in moreinfo:
			outinfo.add_text(k, str(moreinfo[k]))

	return outinfo

def _any2mtime(origname, mtime=None):
	if mtime is None:
		return int(os.path.getmtime(origname))
	else:
		return mtime

def _thumb_path_prefix():
	xdgcache = os.getenv('XDG_CACHE_HOME', os.path.expanduser('~/.cache'))
	return os.path.join(xdgcache, 'thumbnails')

def _gen_filenames(name, size=None):
	uri = _any2uri(name)
	md5uri = md5.new(uri).hexdigest()
	prefix = _thumb_path_prefix()

	if size:
		sizename = _any2size(size)[1]
		return (os.path.join(prefix, sizename, '%s.png' % md5uri),)
	else:
		large = os.path.join(prefix, 'large', '%s.png' % md5uri)
		normal = os.path.join(prefix, 'normal', '%s.png' % md5uri)
		return (large, normal)


# functions that do not create thumbnails
def thumbnail_path(name, size):
	'''Get the path of the potential thumbnail.

	The thumbnail file may or may not exist.

	`name` can be a file path or any URL.

	`size` can be any of 'large', '256' or 256 for large thumbnails
	or 'normal', '128' or 128 for small thumbnails.
	'''

	return _gen_filenames(name, size)[0]

def _fine_existing_thumbnail_path(name, size=None, mtime=None, use_fail_appname=None):
	'''Get the path on an existing thumbnail or an error code'''

	mtime = _any2mtime(name, mtime)

	def do1(filename):
		if not os.path.exists(filename):
			return 1

		try:
			img = PILI.open(filename)
			tntime = int(img.info['Thumb::MTime'])
		except (KeyError, IOError):
			return 2
		else:
			return (mtime != tntime) and 3 or 0

	fns = _gen_filenames(name, size) # FIXME ()
	for filename in fns:
		code = do1(filename)
		if code == 0:
			return (code, filename)

	if use_fail_appname is not None and is_thumbnail_failed(name, use_fail_appname):
		return (4, None)

	return (code, filename)


def existing_thumbnail_path(name, size=None, mtime=None):
	'''Get the path of the thumbnail or None if it doesn't exist.

	`name` can be a file path or any URL.

	If `size` is None, tries with the large thumbnail size, then with the small size.
	'''

	code, filename = _fine_existing_thumbnail_path(name, size, mtime)
	if code == 0:
		return filename
	else:
		return False

def is_thumbnail_failed(name, appname):
	'''Is the thumbnail for `name` failed with `appname` ?'''

	prefix = _thumb_path_prefix()
	apppath = os.path.join(prefix, 'fail', appname)
	md5uri = md5.new(_any2uri(name)).hexdigest()
	return os.path.exists(os.path.join(apppath, md5uri + '.png'))


# functions that create thumbnails
def gen_image_thumbnail(filename, size=None, moreinfo=None, use_fail_appname=None):
	'''Get the path of the thumbnail and create it if necessary.

	Returns None if an error occured.  Creates directories if they don't exist.

	`filename` can't be a URL and must be a local file, in an image format.

	`size` specifies the size of the thumbnail wanted. If there is not thumbnail with that size, it will be created with that size.
	If `size` is None, it looks for any thumbnail size, and creates a large thumbnail if none is found.

	`moreinfo` is a dict that can contain additional key/values to store in the thumbnail file.

	If `use_fail_appname` is not None, it will be used to check failed thumbnails, or to create one if an error occurs.
	'''

	code, thfilename = _fine_existing_thumbnail_path(filename, size)
	if code == 0:
		return thfilename
	elif code == 4:
		return False
	else:
		return force_gen_image_thumbnail(filename, size, moreinfo, use_fail_appname)

def force_gen_image_thumbnail(filename, size=None, moreinfo=None, use_fail_appname=None):
	'''Generate a thumbnail for `filename`, even if the thumbnail existed.

	Returns the path of the thumbnail generated. Creates directories if they don't exist.

	`filename` can't be a URL and must be a local file, in an image format.

	`moreinfo` is a dict that can contain additional key/values to store in the thumbnail file.
	'''

	if size is not None:
		sizeinfo = _any2size(size)
	else:
		sizeinfo = (256, 'large')

	thfilename = _gen_filenames(filename, sizeinfo[1])[0]

	if not os.path.isdir(os.path.dirname(thfilename)):
		os.makedirs(os.path.dirname(thfilename), 0700)

	try:
		img = PILI.open(filename)

		outinfo = _create_pnginfo(_any2uri(filename), int(os.path.getmtime(filename)), moreinfo)
		outinfo.add_text('Thumb::Image::Width', str(img.size[0]))
		outinfo.add_text('Thumb::Image::Height', str(img.size[1]))

		img.thumbnail((sizeinfo[0], sizeinfo[0]), PILI.ANTIALIAS)

		tmppath = tempfile.mkstemp(suffix='.png', dir=os.path.dirname(thfilename))
		os.close(tmppath[0])

		img.save(tmppath[1], pnginfo=outinfo)
		os.rename(tmppath[1], thfilename)
		return thfilename
	except IOError:
		if use_fail_appname is not None:
			put_fail(filename, use_fail_appname, moreinfo=moreinfo)
		return False


def put_thumbnail(origname, thumbpath=None, size=None, mtime=None, moreinfo=None):
	'''Put a thumbnail into the store.

	This method is typically used for thumbnailing non-image files or non-local files.
	The application does the thumbnail on its own, and pushes the thumbnail to the store.

	`origname` is the URL or path of the file thumbnailed.

	`thumbpath` is the path of the thumbnail to put in the store. It can be any local-file.
	If `thumbpath` is None, the application already put the thumbnail at the path returned by `thumbnail_path` and the file should be added information.

	`mtime` is the modification time of the file thumbnailed. If `mtime` is None, `origname` has to be a local file and its mtime will be read.
	(see the module doc for a biref description about mtime).

	`moreinfo` is a dict that can contain additional key/values to store in the thumbnail file.
	'''

	if thumbpath is None:
		thumbpath = existing_thumbnail_path(thumbpath, size, mtime)
		img = PILI.open(thumbpath)
		destpath = thumbpath
	else:
		img = PILI.open(thumbpath)
		if size is None:
			size = max(img.size) # FIXME 64
		destpath = thumbnail_path(origname, size)

	if mtime is None:
		mtime = int(os.path.getmtime(origname))

	outinfo = _create_pnginfo(_any2uri(origname), mtime, moreinfo)
	img.save(destpath, pnginfo=outinfo)

	return destpath

def put_fail(origname, appname, mtime=None, moreinfo=None):
	'''Create a failed thumbnail file.

	Creates directories if they don't exist.

	`mtime` is the modification time of the file thumbnailed. If `mtime` is None, `origname` has to be a local file and its mtime will be read.
	(see the module doc for a biref description about mtime).

	`moreinfo` is a dict that can contain additional key/values to store in the thumbnail file.
	'''

	prefix = _thumb_path_prefix()
	apppath = os.path.join(prefix, 'fail', appname)
	if not os.path.isdir(apppath):
		os.makedirs(apppath, 0700)

	outinfo = _create_pnginfo(_any2uri(origname), _any2mtime(origname, mtime), moreinfo)

	img = PILI.new('RGBA', (1, 1))
	md5uri = md5.new(_any2uri(origname)).hexdigest()
	img.save(os.path.join(apppath, md5uri + '.png'), pnginfo=outinfo)


if __name__ == '__main__':
	print(gen_image_thumbnail(sys.argv[1]))
