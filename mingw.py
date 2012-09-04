#!/usr/bin/python
# Copyright 2011 (C) Daniel Richman. License: GNU GPL 3

import sys
import optparse
import logging
import json
import errno
import os
import os.path
import re
import fcntl
import stat
import hashlib
import urllib
import shutil
import subprocess
import glob

logger = logging.getLogger("builder")

MINGW_NAME = "i586-mingw32msvc"
STD_CONFIGURE = ["--build=i686-pc-linux-gnu", "--host=" + MINGW_NAME,
                 "--enable-static", "--disable-shared"]

class Builder:
    def main(self):
        logging.basicConfig(level=logging.INFO,
                format="[%(asctime)s] %(message)s")
        self.get_options()

        if self.options["verbose"]:
            logging.getLogger().setLevel(level=logging.DEBUG)
        elif self.options["quiet"]:
            logging.getLogger().setLevel(level=logging.WARNING)

        try:
            self.null = open("/dev/null", "w")
            self.check_distro()
            self.check_packages()
            self.open_build_dir()
            self.open_cache_dir()
            self.find_extra_dir()
        except:
            logger.exception("Error whilst setting up")
            sys.exit(1)

        delay_error = False

        try:
            self.build_all()
        except:
            logger.exception("Error in build")
            delay_error = True

        try:
            self.close()
        except:
            logger.exception("Error whilst cleaning up")
            delay_error = True

        if delay_error:
            sys.exit(1)
        else:
            logger.info("Success!")

    def get_options(self):
        parser = optparse.OptionParser(usage="%prog git-source [git-commit]")
        parser.add_option("-d", "--prefix", dest="directory",
                help="build and install dependencies to this directory",
                metavar="DIR", default="w32_build")
        parser.add_option("-c", "--cache", dest="cache",
                help="find and save source tarballs in this directory",
                metavar="DIR", default="w32_cache")
        parser.add_option("-a", "--remake-all", dest="remake_all",
                help="remake everything", action="store_true")
        parser.add_option("-q", "--quiet", dest="quiet",
                help="disable INFO msgs", action="store_true")
        parser.add_option("-v", "--verbose", dest="verbose",
                help="enable DEBUG info", action="store_true")
        parser.add_option("-j", "--make-jobs", dest="make_jobs",
                help="pass -j to make for speedy builds")
        parser.add_option("-b", "--debug", dest="clean_temp_error_exit",
                help="don't clean up if an error occurs, to allow debugging",
                action="store_false", default=True)
        parser.add_option("-o", "--output", dest="output",
                help="save the dl-fldigi installer here", default=".")

        (options, args) = parser.parse_args()
        self.options = options.__dict__

        if len(args) != 1 and len(args) != 2:
            parser.error("Expected single argument: dl-fldigi git location")
        if len(args) == 1:
            args.append(None)

        self.dl_fldigi_source = args[0]
        self.dl_fldigi_commit = args[1]

    def check_packages(self):
        for b in [MINGW_NAME + "-gcc", "makensis", "autoconf",
                  "autoreconf", "git"]:
            self.find_path(b)

    def check_distro(self):
        try:
            self.find_path("lsb_release")
        except:
            logger.warning("Unable to determine linux distro.")
            logger.warning("This script has been tested on Ubuntu lucid "
                           "and Debian squeeze")
            return

        (r, w) = os.pipe()
        read = os.fdopen(r)

        subprocess.call(("lsb_release", "-d"), stdout=w)
        os.close(w)

        line = read.read().strip()
        read.close()

        logger.debug("LSB release: " + line)

        if not ("Ubuntu" in line and "10.04" in line) and \
           not ("Debian" in line and "squeeze" in line):
            logger.warning("This script has been tested on Ubuntu lucid "
                           "and Debian squeeze only!")

    def find_path(self, name):
        for d in os.environ["PATH"].split(":"):
            path = os.path.realpath(os.path.join(d, name))
            if os.path.exists(path):
                return path
        raise Exception("Could not find " + name + " in the path")

    def open_cache_dir(self):
        self.cache = os.path.realpath(self.options["cache"])
        logger.debug("Cache directory is " + self.cache)

        try:
            mode = os.stat(self.cache).st_mode
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise

            os.mkdir(self.cache)
        else:
            if not stat.S_ISDIR(mode):
                raise Exception(self.cache + " is not a directory")

    def open_build_dir(self):
        self.location = os.path.realpath(self.options["directory"])
        logger.debug("Build directory is " + self.location)

        if not re.match(r"^[a-zA-Z0-9_\-/]+$", self.location):
            raise Exception("Some build scripts don't like non a-zA-Z0-9_- in "
                            "the path to the build directory; sorry :-(")

        new_state = False

        try:
            mode = os.stat(self.location).st_mode
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise

            os.mkdir(self.loc())
            os.mkdir(self.loc("pkgconfig"))
            os.mkdir(self.loc("items"))
            new_state = True
        else:
            if not stat.S_ISDIR(mode):
                raise Exception(self.location + " is not a directory")

        self.state_file = open(self.loc("state.json"), "a+")
        fcntl.flock(self.state_file, fcntl.LOCK_EX | fcntl.LOCK_NB)

        if new_state:
            self.state = {"location": self.location}
            self.write_state()
        else:
            self.state = json.load(self.state_file)

            if self.state["location"] != self.location:
                raise Exception("build directory has moved: this will break"
                                "everything!")

    def find_extra_dir(self):
        self.extra = os.path.realpath("w32_extra")
        try:
            os.stat(self.extra)
        except OSError as e:
            raise Exception("Couldn't find directory: w32_extra")

    def loc(self, *args):
        return os.path.join(self.location, *args)

    def cloc(self, *args):
        return os.path.join(self.cache, *args)

    def eloc(self, *args):
        return os.path.join(self.extra, *args)

    def clean_temp(self):
        self.clean_dir("temp")

    def clean_dir(self, *args):
        try:
            os.stat(self.loc(*args))
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise
        else:
            logger.debug("Cleaning " + repr(args))
            shutil.rmtree(self.loc(*args))

        os.mkdir(self.loc(*args))

    def write_state(self):
        self.state_file.seek(0)
        self.state_file.truncate(0)
        json.dump(self.state, self.state_file)
        self.state_file.flush()

    def close(self):
        self.write_state()
        fcntl.flock(self.state_file, fcntl.LOCK_UN)
        self.state_file.close()
        self.null.close()
        if self.options["clean_temp_error_exit"]:
            self.clean_temp()

    def build_all(self):
        self.item("pthreadsw32", "2.8.0")
        self.item("zlib", "1.2.7")
        self.item("libpng", "1.5.12")
        self.item("libjpeg", "6b")
        self.item("fltk", "1.3.0")
        self.item("directx_devel", "3")
        self.item("portaudio", "v19_20111121")
        self.item("samplerate", "0.1.8")
        self.item("sndfile", "1.0.25")
        self.item("xmlrpc", "1.16.42")
        self.item("libtool", "2.4.2")
        self.item("hamlib", "1.2.14")
        self.item("openssl", "1.0.1c")
        self.item("curl", "7.27.0")
        self.item("mingw_fakepath", "1")
        self.item("dl_fldigi", None)

    def item(self, name, version):
        if name not in self.state:
            self.state[name] = False

        if version and self.state[name] == version \
                and not self.options["remake_all"]:
            logger.debug(name + " already built")
            return

        if not version:
            version = "latest"

        self.clean_temp()
        self.clean_dir("items", name)

        logger.info("Building " + name + " " + version)

        try:
            getattr(self, name)()
        except:
            self.clean_dir("items", name)
            raise
        else:
            self.clean_temp()

        logger.debug(name + " done")

        self.state[name] = version
        self.write_state()

    def check_hash(self, f, expect):
        h = self.file_sha512(f)
        return h.lower() == expect.lower()

    def file_sha512(self, f):
        f.seek(0)

        m = hashlib.sha512()
        s = f.read(1024)
        while len(s):
            m.update(s)
            s = f.read(1024)
        return m.hexdigest()

    def download_source(self, url, name, fhash):
        # This doesn't feel particularly pythonic.

        f = open(self.cloc(name), "a+")
        s = None

        try:
            fcntl.flock(f, fcntl.LOCK_SH)

            f.seek(0, os.SEEK_END)
            if not f.tell():
                logger.info("Downloading " + name)
            elif not self.check_hash(f, fhash):
                logger.info("Hash for " + name + " is bad, redownloading")
            else:
                logger.debug("Using cached " + name)
                return f

            fcntl.flock(f, fcntl.LOCK_EX)

            f.truncate(0)
            f.seek(0)

            s = urllib.urlopen(url)
            d = s.read(1024)
            while len(d):
                f.write(d)
                d = s.read(1024)

            if not self.check_hash(f, fhash):
                raise Exception("Downloaded file's hash is bad")

            fcntl.flock(f, fcntl.LOCK_SH)
            f.seek(0)

            return f
        except:
            fcntl.flock(f, fcntl.LOCK_UN)
            f.close()

            if s:
                s.close()

            raise

    def extract_source_tar(self, name):
        os.mkdir(self.loc("temp", "src"))
        self.src_cmd("tar", "-xf", self.cloc(name),
                     "--strip-components=1")

    def rm_f(self, name):
        try:
            os.unlink(name)
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise

    def copy_pkgconfig(self, item, pcname, source=None):
        if not source:
            source = pcname
        self.rm_f(self.loc("pkgconfig", pcname))
        os.symlink(self.loc("items", item, "lib", "pkgconfig", source),
                   self.loc("pkgconfig", pcname))

    def src_cmd(self, *args, **kwargs):
        logger.debug("Executing: " + repr(args) + " " + repr(kwargs))

        if not self.options["verbose"]:
            kwargs["stdout"] = self.null
            kwargs["stderr"] = self.null

        if "cwd" not in kwargs:
            kwargs["cwd"] = self.loc("temp", "src")

        ret = subprocess.call(args, **kwargs)

        if ret != 0:
            raise Exception("subprocess error exited " + repr(args))

    def make(self, *args, **kwargs):
        args = list(args)
        args.insert(0, "make")
        if self.options["make_jobs"]:
            args.append("-j" + self.options["make_jobs"])
        self.src_cmd(*args, **kwargs)

    def configure(self, *args, **kwargs):
        args = list(args)
        args.insert(0, "./configure")

        if "flag_items" in kwargs:
            CPPFLAGS = []
            LDFLAGS = []

            for i in kwargs["flag_items"]:
                CPPFLAGS.append(" -I" + self.loc("items", i, "include"))
                LDFLAGS.append(" -L" + self.loc("items", i, "lib"))

            del kwargs["flag_items"]

            args.append("CPPFLAGS=" + " ".join(CPPFLAGS))
            args.append("LDFLAGS=" + " ".join(LDFLAGS))

        self.src_cmd(*args, **kwargs)

    def pthreadsw32(self):
        self.download_source("ftp://sourceware.org/pub/pthreads-win32/"
            "pthreads-w32-2-8-0-release.tar.gz", "pthreadsw32.tar.gz",
            "d86040b18641b52f2de81468e06e2885d0f0ed47cc5c9c90ca33614ed53ddd60"
            "167723b46eb477d905ad69f93b6b9002762589ba6c351f78a8041109cdbf287e")
        self.extract_source_tar("pthreadsw32.tar.gz")
        self.make("CROSS=" + MINGW_NAME + "-", "clean", "GC-inlined")

        for d in ["include", "lib"]:
            os.mkdir(self.loc("items", "pthreadsw32", d))

        for f in ["pthread.h", "sched.h", "semaphore.h"]:
            shutil.copy(self.loc("temp", "src", f),
                        self.loc("items", "pthreadsw32", "include"))

        shutil.copy(self.loc("temp", "src", "libpthreadGC2.a"),
                    self.loc("items", "pthreadsw32", "lib", "libpthreadGC2.a"))
        shutil.copy(self.loc("temp", "src", "pthreadGC2.dll"),
                    self.loc("items", "pthreadsw32", "lib", "pthreadGC2.dll"))
        os.symlink(self.loc("items", "pthreadsw32", "lib", "libpthreadGC2.a"),
                   self.loc("items", "pthreadsw32", "lib", "libpthread.a"))

    def zlib(self):
        self.download_source("http://zlib.net/zlib-1.2.7.tar.gz",
            "zlib.tar.gz",
            "b1c073ad26684e354f7c522c14655840592e03872bc0a94690f89cae2ff88f14"
            "6fce1dad252ff27a889dac4a32ff9f8ab63ba940671f9da89e9ba3e19f1bf58d")
        self.extract_source_tar("zlib.tar.gz")

        env = os.environ.copy()
        for var, binary in [("CC", "gcc"), ("AR", "ar"), ("RANLIB", "ranlib")]:
            env[var] = MINGW_NAME + "-" + binary
        env["CFLAGS"] = "-O2"

        self.configure("--prefix=" + self.loc("items", "zlib"), env=env)
        self.make("LDSHAREDLIBC=")
        self.make("install")

        # Remove docs
        shutil.rmtree(self.loc("items", "zlib", "share"))

    def libpng(self):
        self.download_source("http://downloads.sourceforge.net/libpng/"
            "libpng-1.5.12.tar.gz", "libpng.tar.gz",
            "dbefad00fa34f4f21dca0f1e92e95bd55f1f4478fa0095dcf015b4d06f0c823f"
            "f11755cd777e507efaf1c9098b74af18f613ec9000e5c3a5cc1c7554fb5aefb8")
        self.extract_source_tar("libpng.tar.gz")

        self.configure("--prefix=" + self.loc("items", "libpng"),
                flag_items=["zlib"], *STD_CONFIGURE)
        self.make()
        self.make("install")

        self.copy_pkgconfig("libpng", "libpng.pc")
        os.symlink(self.loc("items", "libpng", "include", "libpng15"),
                   self.loc("items", "libpng", "include", "libpng"))

        shutil.rmtree(self.loc("items", "libpng", "share"))

    def libjpeg(self):
        self.download_source("http://downloads.sourceforge.net/libjpeg/"
            "jpegsrc.v6b.tar.gz", "libjpeg6b.tar.gz",
            "5d37d3695105fc345ca269ab98cd991472e5de72f702c9a8a652a7d114a40eb9"
            "9670c69a87ecb24bf64e96318fc0ee2bcb44c497d9d3d2a67378c99e4eb348fe")
        self.extract_source_tar("libjpeg6b.tar.gz")

        self.configure("--prefix=" + self.loc("items", "libjpeg"),
                "CC=" + MINGW_NAME + "-gcc")
        self.make("libjpeg.a",
                "AR=" + MINGW_NAME + "-ar rc",
                "AR2=" + MINGW_NAME + "-ranlib")

        for d in ["include", "lib"]:
            os.mkdir(self.loc("items", "libjpeg", d))
        self.make("install-lib")

    def fltk(self):
        self.download_source("http://ftp.easysw.com/pub/fltk/1.3.0/"
            "fltk-1.3.0-source.tar.gz", "fltk.tar.gz",
            "a7adf9def90b143bc7ff54ac82fe9f6812b49209ab4145aada45210a3c314f9d"
            "91ae413240a8c57492826eca011aa147c68a131a9fe20bf221e7bc70c6c908ee")
        self.extract_source_tar("fltk.tar.gz")
        with open(self.eloc("mingw-fltk.patch")) as p:
            self.src_cmd("patch", "-p1", stdin=p)

        self.src_cmd("autoconf")
        self.configure("--prefix=" + self.loc("items", "fltk"),
                "--enable-threads", *STD_CONFIGURE,
                flag_items=["libpng", "libjpeg", "zlib", "pthreadsw32"])
        # DIRS=src switches off building in documentation, test, fluid
        # directories.
        self.make("DIRS=src")
        self.make("DIRS=src", "install")

        # fltk-config binary

    def directx_devel(self):
        os.mkdir(self.loc("items", "directx_devel", "include"))
        shutil.copy(self.eloc("dsound.h"),
                    self.loc("items", "directx_devel", "include"))

    def portaudio(self):
        self.download_source("http://www.portaudio.com/archives/"
            "pa_stable_v19_20111121.tgz", "portaudio.tar.gz",
            "e9d039313ce27ae1f643ef2509ddea7ac6aadca5d39f2f2a4f0ccd8a3661a5a5"
            "6a29e666300d3d4418cab6231ee14b3c09eb83dca0dd3326e1f24418d035abb2")
        self.extract_source_tar("portaudio.tar.gz")

        self.configure("--prefix=" + self.loc("items", "portaudio"),
                "--with-winapi=wmme,directx",
                "--with-dxdir=" + self.loc("items", "directx_devel"),
                *STD_CONFIGURE)
        self.make()
        self.make("install")

        self.copy_pkgconfig("portaudio", "portaudio-2.0.pc")

    def samplerate(self):
        self.download_source("http://www.mega-nerd.com/SRC/"
            "libsamplerate-0.1.8.tar.gz", "samplerate.tar.gz",
            "85d93df24d9d62e7803a5d0ac5d268b2085214adcb160e32fac316b12ee8a0ce"
            "36ccfb433a3c0a08f6e3ec418a5962bdb84f8a11262286a9b347436983029a7d")
        self.extract_source_tar("samplerate.tar.gz")

        self.configure("--prefix=" + self.loc("items", "samplerate"),
                "--disable-fftw", "--disable-sndfile", # Used in example bins
                *STD_CONFIGURE)
        self.make()
        self.make("install")

        shutil.rmtree(self.loc("items", "samplerate", "bin"))
        shutil.rmtree(self.loc("items", "samplerate", "share"))

        self.copy_pkgconfig("samplerate", "samplerate.pc")

    def sndfile(self):
        self.download_source("http://www.mega-nerd.com/libsndfile/files/"
            "libsndfile-1.0.25.tar.gz", "sndfile.tar.gz",
            "4ca9780ed0a915aca8a10ef91bf4bf48b05ecb85285c2c3fe7eef1d46d3e0747"
            "e61416b6bddbef369bd69adf4b796ff5f61380e0bc998906b170a93341ba6f78")
        self.extract_source_tar("sndfile.tar.gz")

        self.configure("--prefix=" + self.loc("items", "sndfile"),
                "--disable-external-libs", "--disable-sqlite", *STD_CONFIGURE)
        self.make()
        self.make("install")

        shutil.rmtree(self.loc("items", "sndfile", "bin"))
        shutil.rmtree(self.loc("items", "sndfile", "share"))

        self.copy_pkgconfig("sndfile", "sndfile.pc")

    def xmlrpc(self):
        self.download_source("http://downloads.sourceforge.net/xmlrpc-c/"
            "xmlrpc-c-1.16.42.tgz", "xmlrpc-c.tar.gz",
            "e7307631e6d2915eba7811570b8cb236994b82221458657347acf8703d9bf3c1"
            "5e9d46c15a90c3cce0af81237286d9efbe5ee471881e1fc2a2a952beb1fdafb0")
        self.extract_source_tar("xmlrpc-c.tar.gz")
        with open(self.eloc("mingw-xmlrpc-c.patch")) as p:
            self.src_cmd("patch", "-p1", stdin=p)

        self.src_cmd("autoconf")
        self.configure("--prefix=" + self.loc("items", "xmlrpc"),
                "CC=" + MINGW_NAME + "-gcc",
                "--disable-wininet-client", "--disable-curl-client",
                "--disable-libwww-client", *STD_CONFIGURE)

        arargs = ["AR=" + MINGW_NAME + "-ar",
                  "RANLIB=" + MINGW_NAME + "-ranlib"]
        self.make("BUILDTOOL_CC=gcc", "BUILDTOOL_CCLD=gcc",
                  "CFLAGS_PERSONAL=-U_UNIX", *arargs)
        self.make("install", *arargs)

        # xmlrpc-c-config binary

    def libtool(self):
        self.download_source("http://ftpmirror.gnu.org/libtool/"
            "libtool-2.4.2.tar.gz", "libtool.tar.gz",
            "0e54af7bbec376f943f2b8e4f13631fe5627b099a37a5f0252e12bade76473b0"
            "a36a673529d594778064cd8632abdc43d8a20883d66d6b27738861afbb7e211d")
        self.extract_source_tar("libtool.tar.gz")

        self.configure("--prefix=" + self.loc("items", "libtool"),
                *STD_CONFIGURE)
        self.make()
        self.make("install")

    def hamlib(self):
        self.download_source("http://downloads.sourceforge.net/hamlib/"
            "hamlib-1.2.14.tar.gz", "hamlib.tar.gz",
            "a209048750e7e55a2386af436e01741ffab0338aa21db8d1a82eb0072a5161e2"
            "9c722c5cc1ea5d021dac9a069f7c4fe3a41c73969a6d39cdee88f809c1ea4354")
        self.extract_source_tar("hamlib.tar.gz")

        self.configure("--prefix=" + self.loc("items", "hamlib"),
                "--without-rigmatrix", "--without-rpc-backends",
                "--without-winradio", "--without-gnuradio", "--without-usrp",
                "--without-cxx-binding", "--without-perl-binding",
                "--without-tcl-binding", "--without-python-binding",
                flag_items=["pthreadsw32", "libtool"],
                *STD_CONFIGURE)

        # Ugh...
        self.src_cmd("sed", "-i", "s/ tests doc$/ doc/", "Makefile")
        self.src_cmd("sed", "-i", "s/^int usleep/\/\//",
                     "lib/win32termios.h") # int != long
        os.mkdir(self.loc("temp", "src", "libltdl"))
        self.make("DEFS=-DHAVE_SLEEP -DHAVE_CONFIG_H")
        self.make("install")

        self.copy_pkgconfig("hamlib", "hamlib.pc")

    def openssl(self):
        self.download_source("http://www.openssl.org/source/"
            "openssl-1.0.1c.tar.gz", "openssl.tar.gz",
            "14f766daab0828a2f07c65d6da8469a4a5a2b839ff3da188538c4e2db3e3e2f3"
            "7217fb37e269617fb438463b75fb77dab0b155f36831ff48edbc9e7f2903ebd3")
        self.extract_source_tar("openssl.tar.gz")

        self.src_cmd("/bin/bash", "./Configure", "mingw",
                "--prefix=" + self.loc("items", "openssl"))

        self.make("CC=" + MINGW_NAME + "-gcc",
                  "AR=" + MINGW_NAME + "-ar r",
                  "RANLIB=" + MINGW_NAME + "-ranlib",
                  "DIRS=crypto ssl engines",
                  "all")
        self.make("DIRS=crypto ssl engines", "install_sw")

        self.copy_pkgconfig("openssl", "openssl.pc")
        self.copy_pkgconfig("openssl", "libssl.pc")
        self.copy_pkgconfig("openssl", "libcrypto.pc")

    def curl(self):
        self.download_source("http://curl.haxx.se/download/curl-7.27.0.tar.gz",
            "curl.tar.gz",
            "d701631e897464d92582a77f13e8aed17f10ee1d284007af3c1435a6c2263c90"
            "b4ba0334b9b41b20e34518d5d5be7cf0fd7c1ef0092bc592bd137577f5faf213")
        self.extract_source_tar("curl.tar.gz")

        self.configure("--prefix=" + self.loc("items", "curl"),
                "--with-zlib=" + self.loc("items", "zlib"),
                "--with-ssl=" + self.loc("items", "openssl"),
                "--without-ldap-lib",
                "--disable-manual", *STD_CONFIGURE)
        self.make()
        self.make("install")

        self.copy_pkgconfig("curl", "libcurl.pc")
        shutil.rmtree(self.loc("items", "curl", "share"))

    def mingw_fakepath(self):
        for n in ["addr2line", "ar", "as", "c++", "cc", "c++filt", "cpp",
                  "dlltool", "dllwrap", "g++", "gcc", "gccbug",
                  "gcov", "gfortran", "gprof", "ld", "nm", "objcopy",
                  "objdump", "ranlib", "readelf", "size", "strings",
                  "strip", "windmc", "windres"]:
            target_name = MINGW_NAME + "-" + n
            os.symlink(self.find_path(target_name),
                       self.loc("items", "mingw_fakepath", n))

    def dl_fldigi(self):
        self.src_cmd("git", "clone", self.dl_fldigi_source,
                     self.loc("temp", "src"), cwd=None)
        if self.dl_fldigi_commit:
            self.src_cmd("git", "checkout", self.dl_fldigi_commit)

        self.src_cmd("git", "submodule", "init")
        self.src_cmd("git", "submodule", "update")
        self.src_cmd("autoreconf", "-vfi")

        env = os.environ.copy()
        env["PKG_CONFIG_LIBDIR"] = self.loc("pkgconfig")

        self.configure("--disable-nls", "--disable-flarq",
                   "--without-pulseaudio",
                   "--with-ptw32=" + self.loc("items", "pthreadsw32"),
                   "FLTK_CONFIG=" + self.loc("items", "fltk", "bin",
                                             "fltk-config"),
                   "XMLRPC_C_CONFIG=" + self.loc("items", "xmlrpc", "bin",
                                                 "xmlrpc-c-config"),
                   "X_CFLAGS=-DXMD_H", # Inhibit libjpeg crud
                   "LIBS=-lltdl",
                   flag_items=["libjpeg", "zlib", "openssl", "libtool"],
                   env=env,
                   *STD_CONFIGURE)
        self.make()

        self.make("hamlib-static", env=env)
        self.make("nsisinst")

        search = glob.glob(self.loc("temp", "src", "src",
                                    "dl-fldigi-*_setup.exe"))
        installer = search[0]

        shutil.copy(installer, self.options["output"])
        logger.info("Saved binary " + os.path.basename(installer) + " to " +
                    self.options["output"])

if __name__ == "__main__":
    Builder().main()
