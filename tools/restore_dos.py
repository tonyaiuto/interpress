#!/usr/bin/env python3
# MIT License
#
# Copyright (c) 2021 tonyaiuto
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""Restore MSDOS 2.x backup images.

Research from the FreeDOS project at:
http://www.ibiblio.org/pub/micro/pc-stuff/freedos/files/dos/restore/brtecdoc.htm
"""

from collections import defaultdict
import os
import sys


def loadshort(blob, at):
  return blob[at+1] << 8 | blob[at]


class BackupID(object):

  def __init__(self, path, data):
    self.errors = []
    self.path = path
    md_flag = data[0]
    assert md_flag == 0x00 or md_flag == 0xff
    self.last = (md_flag == 0xff)
    self.sequence = loadshort(data, 1)
    self.year = loadshort(data, 3)
    self.day = data[5]
    self.month = data[6]
    for i in range(7, len(data)):
      if data[i] != 0:
        self.errors.append('unexpected non-zero at %d: %d' % (i, data[i]))

  def __str__(self):
    ret = 'Disk %d' % self.sequence
    if self.last:
      ret += ' last'
    ret += ', %4d-%02d-%02d' % (self.year, self.month, self.day)
    if self.errors:
      ret += '## (%s)' % ', '.join(self.errors)
    return ret

  @staticmethod
  def from_file(file_path):
    with open(file_path, 'rb') as inp:
      blob = inp.read()
      assert len(blob) == 128
      return BackupID(file_path, blob)


class BackupFile(object):

  def __init__(self, path, data):

    def decode(b):
      cb = chr(b)
      if str.isascii(cb) and b >= ord(' '):
        return cb
      return '%' + ('%02x' % b)

    self.errors = []
    self.image_path = path
    md_flag = data[0]
    self.last = (md_flag == 0xff)
    self.sequence = loadshort(data, 1)
    self.unknown = loadshort(data, 3)
    self.path_len = data[0x53]
    if self.path_len == 0 or self.path_len > 78:
      self.errors.append('unexpected file path len: %d' % self.path_len)
      self.path = 'bad_file'
    else:
      raw_path = data[5:5+self.path_len]
      # Trim needless trailing NUL
      if raw_path[-1] == 0:
        raw_path = raw_path[0:-1]
      try:
        self.path = raw_path.decode('ascii')
      except UnicodeDecodeError:
        self.path = ''.join([decode(b) for b in raw_path])

    self.content = data[0x80:]
    if md_flag != 0x00 and md_flag != 0xff:
      self.errors.append('%s: unexpected flag value 0x%02x' % (self.path, md_flag))

  def __str__(self):
    if self.is_complete:
      status = 'complete'
    else:
      status = 'seq %d' % self.sequence
      if self.last:
        status += ' last'
    ret = '%s (%s)' % (self.path, status)
    if self.errors:
      ret += '## (%s)' % ', '.join(self.errors)
    return ret

  @property
  def is_complete(self):
    return self.last and self.sequence == 1

  @staticmethod
  def from_file(file_path):
    with open(file_path, 'rb') as inp:
      blob = inp.read()
      return BackupFile(file_path, blob)


def dbg_files():
  for root, dirs, files in os.walk('disks'):
     for name in files:
       path  = os.path.join(root, name)
       if name.endswith('.img'):
         continue
       if name == 'cmd.sh':
         continue
       if name == 'BACKUPID.@@@':
         id = BackupID.from_file(path)
         print(path, id)
         continue
       f = BackupFile.from_file(path)
       print(path, f)


class Restore(object):

  def __init__(self):
    self.completed = set()  # all paths done
    self.done = {}
    self.errors = []
    self.partials = defaultdict(list)
    self.root = '.'
    self.sets = {}
    self.verbose = False

  def add_backup_id(self, path):
    id = BackupID.from_file(path)
    self.sets[path] = id
    if self.verbose:
      print(path, id)

  def process_file(self, file_path):
    b_file = BackupFile.from_file(file_path)
    if b_file.errors:
      print('SKIPPING:', file_path, b_file)
      self.errors.append(b_file)
      return

    #if b_file.path in self.completed:
    #  print('##ERROR got file again:', b_file)
    #  return
    if b_file.is_complete:
      self.write_file(b_file)
      self.completed.add(b_file.path)
    else:
      slices = self.partials[b_file.path]
      slices.append(b_file)
      if self.got_all_slices(slices):
        self.write_slices(slices)
        self.completed.add(b_file.path)
        del self.partials[b_file.path]

  @staticmethod
  def got_all_slices(slices):
    s = sorted(slices, key=lambda x: x.sequence)
    for i,s_i in enumerate(s):
      if i + 1 != s_i.sequence:
        return False
      if s_i.last:
        if i != len(s)-1:
          error('inconsistant', str(','.join(s)))
    return s[len(s)-1].last

  def process_disk(self, disk_path):
    for root, dirs, files in os.walk(disk_path):
      for name in files:
         if name.endswith('.img'):
           continue
         if name == 'cmd.sh':
           continue
         if name == 'BACKUPID.@@@':
           continue
         self.process_file(os.path.join(root, name))

  def write_file(self, f):
    self.write_slices([f])

  def write_slices(self, slices):
    o_path = slices[0].path.lower().replace('\\', '/')
    if o_path.startswith('/'):
      o_path = o_path[1:]
    ls = len(slices)
    if self.verbose:
      if ls == 1:
        msg = str(slices[0])
      else:
        msg = '%s (%d parts)' % (slices[0].path, ls)
      print('writing:', msg, 'as', o_path)
    os.makedirs(os.path.dirname(o_path), exist_ok=True)
    content = b''
    with open(os.path.join(self.root, o_path), 'wb') as out:
      for s in sorted(slices, key=lambda x: x.sequence):
        out.write(s.content)
        content += s.content
    before = self.done.get(o_path)
    if before and before != content:
      print('content changed on:', o_path)


def gather_image_headers(root):
  ret = []
  for root, dirs, files in os.walk(root):
     for name in files:
       if name == 'BACKUPID.@@@':
         ret.append(os.path.join(root, name))
  return sorted(ret)

# dbg_files()


def restore_all(top):
  headers = gather_image_headers(top)
  rest = Restore()
  for header in headers:
    rest.add_backup_id(header)
    rest.process_disk(os.path.dirname(header))
  if rest.partials:
    print('Unfinished files:')
    for p in rest.partials:
      print('    ', p)


# This obvsiously will not work for you.
restore_all('disks')
