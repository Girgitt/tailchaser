"""Module that contains the the chaser (producer) classes.

.. moduleauthor:: Thanos Vassilakis <thanosv@gmail.com>

"""

import binascii
import glob
import gzip
# import hashlib
import logging.handlers
import os
import pickle
import re
import shutil
import sys
import tempfile
import time

import six

log = logging.getLogger(__name__)
xrange = six.moves.xrange

SIG_SZ = 256


def slugify(value):
    """
    Convert spaces to hyphens.
    Remove characters that aren't alphanumerics, underscores, or hyphens.
    Convert to lowercase. Also strip leading and trailing whitespace.
    """
    value = re.sub('[^\w\s-]', '', value).strip().lower()
    return re.sub('[-\s]+', '-', value)


class Args:
    pass


class Tailer(object):
    VERBOSE = False
    DONT_FOLLOW = False
    DRYRUN = False
    DONT_BACKFILL = False
    READ_PERIOD = 1
    CLEAR_CHECKPOINT = False
    READ_PAUSE = 0
    TMP_DIR = None
    ONLY_BACKFILL = False

    STARTING = 'STARTING'
    STOPPED = 'STOPPED'
    RUNNING = 'RUNNING'
    WAITING = 'WAITING'

    def __init__(self,
                 only_backfill=ONLY_BACKFILL,
                 dont_backfill=DONT_BACKFILL,
                 read_period=READ_PERIOD,
                 clear_checkpoint=CLEAR_CHECKPOINT,
                 read_pause=READ_PAUSE,
                 temp_dir=TMP_DIR):

        self.config = Args()
        self.config.dont_backfill = dont_backfill
        self.config.only_backfill = only_backfill
        self.config.clear_checkpoint = clear_checkpoint
        self.config.read_period = read_period
        self.config.read_pause = read_pause
        self.config.temp_dir = temp_dir if temp_dir else tempfile.mkdtemp()
        self.state = self.STARTING

    def startup(self):
        pass

    def shutdown(self):
        pass

    def at_eof(self):
        pass

    def run(self, source_pattern, receiver=None):
        self.startup()
        self.config.checkpoint_filename = self.make_checkpoint_filename(source_pattern)
        if self.config.clear_checkpoint and os.path.exists(self.config.checkpoint_filename):
            os.unlink(self.config.checkpoint_filename)
        file_info = 'Not Assigned'
        try:
            while self.state != self.STOPPED:
                ticks = time.time()
                try:
                    checkpoint = self.load_checkpoint()
                    is_backfill_file_info = self.next_to_process(source_pattern, checkpoint)
                    if is_backfill_file_info:
                        is_backfill, file_info = is_backfill_file_info
                        if file_info:
                            if is_backfill or self.config.only_backfill:
                                producer = self.backfill(file_info)
                            else:
                                producer = self.tail(file_info)
                            for file_tailed, checkpoint, record in producer:
                                self.handoff(file_tailed, checkpoint, record, receiver)
                                self.save_checkpoint(checkpoint)
                                if not is_backfill and self.config.read_period:
                                    time_spent = time.time() - ticks
                                    if time_spent > self.config.read_period:
                                        time.sleep(self.config.read_pause)
                                        break
                    elif self.config.only_backfill:
                        break
                except KeyboardInterrupt:
                    raise
                except:
                    raise
                time.sleep(3)
                self.at_eof()
        finally:
            self.shutdown()

    def handoff(self, file_tailed, checkpoint, record, receiver=None):
        if receiver:
            receiver.send((file_tailed, checkpoint, record))
        else:
            sys.stdout.write(record)

    def next_to_process(self, source_pattern, checkpoint):
        log.debug("next_to_process: %s %s", source_pattern, checkpoint)
        with_stats = [(file_name, os.stat(file_name)) for file_name in glob.glob(source_pattern)]
        # log.debug("with_stats: %s", glob.glob(source_pattern))
        for pos, (file_name, stat) in enumerate(sorted(with_stats, key=lambda x: x[1].st_mtime)):
            # print pos, (file_name, stat), self.make_sig(file_name, stat)
            if stat.st_mtime > checkpoint[1]:
                # print 'younger', (stat.st_mtime > checkpoint[1], stat.st_mtime, checkpoint[1])
                sig = self.make_sig(file_name, stat)
                if sig != checkpoint[0]:
                    # print 'new file'
                    checkpoint = (sig, stat.st_mtime, 0)
                else:
                    # print 'same as before'
                    checkpoint = (sig, stat.st_mtime, checkpoint[2])
                self.state = 'RUNNING'
                return (pos + 1) != len(with_stats), (file_name, checkpoint)
            elif stat.st_mtime == checkpoint[1]:
                if (pos + 1) != len(with_stats):
                    continue
                sig = self.make_sig(file_name, stat)
                if sig == checkpoint[0]:
                    if self.state == self.RUNNING:
                        self.state = self.WAITING
                        return
                checkpoint = (checkpoint[0], stat.st_mtime, checkpoint[2])
                return (pos + 1) != len(with_stats), (file_name, checkpoint)

                #         file_info = None
                #         log.info("with_stats after sort: %s", [f[0] for f in with_stats])
                #         #sys.exit()
                #         while with_stats:
                #             log.debug("with_stats: %s", with_stats)
                #             (file_name, file_stat) = with_stats[0]
                #             with_stats = with_stats[1:]
                #             file_info = self.should_process(file_name, file_stat, checkpoint)
                #             if file_info:
                #                 break
                #         log.debug("file_info: %s", file_info)
                #         if file_info:
                #             if with_stats:
                #                 new_file = os.path.join(self.config.temp_dir, str(file_info[1][0]))
                #                 self.copy(file_info[0], new_file)
                #                 file_info = new_file, file_info[1]
                #                 return True, file_info
                #             return False, file_info

    # def should_process(self, file_to_check, stat, checkpoint):
    #
    #     checkpoint_sig, checkpoint_mtime, checkpoint_offset = checkpoint
    #     # stat = os.stat(file_to_check)
    #     log.info('should_process %s', (file_to_check, stat, checkpoint))
    #     retval = None
    #     if not checkpoint:
    #         log.info("%s - no checkpoint", file_to_check)
    #         retval = file_to_check, (self.make_sig(file_to_check, stat), stat.st_mtime, 0)
    #     else:
    #         if checkpoint_mtime < stat.st_mtime:
    #             log.info("%s -- newer than checkpoint %s %s", file_to_check, checkpoint_mtime, stat.st_mtime)
    #             retval = file_to_check, (self.make_sig(file_to_check, stat), stat.st_mtime, 0)
    #         else:
    #             sig = self.make_sig(file_to_check, stat)
    #             if sig == checkpoint_sig:
    #                 log.info("%s --- same as checkpoint", file_to_check)
    #                 if checkpoint_offset < stat.st_size:
    #                     log.info("%s ---- size changed since  checkpoint", file_to_check)
    #                     retval = file_to_check, (self.make_sig(file_to_check, stat), stat.st_mtime, checkpoint_offset)
    #                 elif checkpoint[2] == stat.st_size:
    #                     log.info('same sig and size: %d', checkpoint_offset)
    #     return retval

    def backfill(self, file_info):
        file_to_process, (sig, st_mtime, offset) = file_info
        log.debug("backfill: %s %s", file_to_process, offset)
        file_to_process = os.path.join(self.config.temp_dir, str(file_info[1][0]))
        self.copy(file_info[0], file_to_process)
        for offset, record in self.process(file_to_process, sig, st_mtime, offset):
            if not record:
                break
            yield file_to_process, (sig, st_mtime, offset), record

    def tail(self, file_info):
        file_to_process, (sig, st_mtime, offset) = file_info
        log.debug("tail: %s %s", file_to_process, offset)
        while True:
            offset, record = six.next(self.process(file_to_process, sig, st_mtime, offset))
            if record:
                yield "%d" % self.make_sig(file_to_process), (sig, st_mtime, offset), record

    def process(self, filename, sig, st_mtime, offset):
        with self.open(filename) as file_to_tail:
            log.debug("seeking: %s %s", filename, offset)
            file_to_tail.seek(offset, 0)
            for offset, record in self.read_record(file_to_tail):
                yield offset, record

    def read_record(self, file_to_tail):
        bytes = file_to_tail.tell()
        for record in file_to_tail:
            bytes += len(record)
            yield bytes, record
        pass

    @staticmethod
    def make_checkpoint_filename(source_pattern, path=None):
        if not path:
            path = os.path.join(os.path.expanduser("~"), '.tailchase')
        if not os.path.exists(path):
            log.debug("making checkpoint path: %s", path)
            os.makedirs(path)
        return os.path.join(path, os.path.basename(slugify(source_pattern) + '.checkpoint'))

    def open(self, file_name):
        if file_name.endswith('.gz'):
            log.debug("gzip file: %s", file_name)
            return gzip.open(file_name, 'rb')
        else:
            return open(file_name, 'rb')

    def copy(self, src, dst):
        log.debug("copying: %s to %s", src, dst)
        return shutil.copy2(src, dst)

    @classmethod
    def make_sig(cls, file_to_check, stat=None):
        return binascii.crc32(six.b(open(file_to_check).read(SIG_SZ))) & 0xffffffff
        # return hashlib.sha224(open(file_to_check).read(SIG_SZ)).hexdigest()

    def load_checkpoint(self):
        checkpoint_filename = self.config.checkpoint_filename
        try:
            sig, mtime, offset = pickle.load(open(checkpoint_filename, 'rb'))
            log.debug('loaded: %s %s', checkpoint_filename, (sig, mtime, offset))
            return sig, mtime, offset
        except (IOError, EOFError):
            log.debug('failed to load: %s', checkpoint_filename)
            return '', 0, 0

    def save_checkpoint(self, checkpoint):
        log.debug('dumping %s %s', self.config.checkpoint_filename, checkpoint)
        return pickle.dump(checkpoint, open(self.config.checkpoint_filename, 'wb'))

# if __name__ == '__main__':
#     def fx():
#         while True:
#             record = yield ()
#             six.print_(record)
#
#     def gx():
#         while True:
#             record = yield ()
#             open(os.path.basename(record[0]) + '.2', 'ab').write(record[2])
#
#     f = gx()
#     f.send(None)
#     logging.basicConfig(format='%(levelname)s:%(message)s', level=logging.DEBUG)
#     Tailer(only_backfill=True, clear_checkpoint=True).run(sys.argv[1], f)