"""
Upload and store arXiv source files:

- harvesting of metadata and PDF must have been done first before adding the source files 

- the access to latex source archive files must be done via a S3 paid-access bucket, so S3 account info
are necessary. This S3 access account will be billed for retrieving the arXin source archive files. 

- the target storage of choice must be explicitely given in the command line, so that we can store in
something different from S3 (or with a different account than the one for accessing the arXiv sources)
"""

import sys
import os
import shutil
import gzip
import json
import requests
import uuid
import subprocess
import argparse
import time
from concurrent.futures import ThreadPoolExecutor
from random import randint, choices
from tqdm import tqdm
from zipfile import ZipFile

# support for S3
import arxiv_harvester.S3 as S3

# support for SWIFT object storage
import arxiv_harvester.swift as swift

#from google.cloud import storage
import urllib3

# logging
import logging
import logging.handlers
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.getLogger("urllib3").setLevel(logging.ERROR)
logging.getLogger("keystoneclient").setLevel(logging.ERROR)
logging.getLogger("swiftclient").setLevel(logging.ERROR)

from arxiv_harvester.harvester import _load_config

import pickle
import lmdb

import tarfile
from zipfile import ZipFile

# init LMDB
map_size = 200 * 1024 * 1024 * 1024 

class ArXivSourceHarvester(object):

    def __init__(self, config):
        self.config = config

        self._init_lmdbs()

        self.s3 = None
        if "bucket_name" in self.config and len(self.config["bucket_name"].strip()) > 0:
            self.s3 = S3.S3(self.config)

        self.swift = None
        if "swift" in self.config and len(self.config["swift"])>0 and "swift_container" in self.config and len(self.config["swift_container"])>0:
            self.swift = swift.Swift(self.config)

        self.s3_source = None
        if "arxiv-source" in self.config:
            if "bucket_name" in self.config["arxiv-source"] and len(self.config["arxiv-source"]["bucket_name"].strip()) > 0:
                self.s3_source = S3.S3(self.config["arxiv-source"])

    def _init_lmdbs(self):
        # create the data path if it does not exist 
        if not os.path.isdir(self.config["data_path"]):
            try:  
                os.makedirs(self.config["data_path"])
            except OSError:  
                logging.exception("Creation of the directory %s failed" % self.config["data_path"])
            else:  
                logging.debug("Successfully created the directory %s" % self.config["data_path"])

        # open in write mode
        envFilePath = os.path.join(self.config["data_path"], 'sources')
        self.env_source = lmdb.open(envFilePath, map_size=map_size)

    def harvest_sources(self):
        # we download archive one after the other from the S3 bucket
        # we extract the resources of the current archive, and move the resources according to the 
        # arxiv identifier at the right place
        # when done the archive is deleted or stored, and we download the next one
        list_files = self.get_list_source_files()

        print("Number of source archive files:", str(len(list_files)))

        for file in list_files:
            # download the archive file, these are tar files
            dest_path = os.path.join(self.config["data_path"], file)
            #dest_path = self.s3_source.download_file(file, dest_path)
            print("downloaded", dest_path)
            if dest_path == None:
                logging.error("S3 download failed for " + file)
            else:
                with tarfile.open(dest_path) as tar:
                    for member in tar.getmembers():
                        # get gzip files and ignore PDF files, the gzip files are actually tar gzip files with the sources inside
                        if not member.name.endswith(".gz"):
                            continue
                        identifier = os.path.basename(member.name)
                        identifier = identifier.replace(".gz", "")
                        print("identifier:", identifier)
                        extraction_path = self.config["data_path"]
                        try:
                            tar.extract(member=member, path=extraction_path)
                            extracted_path = os.path.join(extraction_path, member.name)
                            local_zip_file = self.harvest_source(extracted_path, identifier)

                            # upload zip file if not empty
                            if local_zip_file != None:
                                collection, prefix, number = _generate_storage_components(arxiv_id)
                                if collection == 'arxiv':
                                    full_number = prefix+"."+number
                                else:
                                    full_number = prefix+number

                                # temporary place to download the file
                                zip_dest_path = os.path.join(self.config["data_path"], full_number + ".zip")
                                self.store_file(zip_file, identifier)
                        finally:
                            if extracted_path != None and os.path.isfile(extracted_path):
                                ind = member.name.find("/")
                                if ind != -1:
                                    member_root = member.name[:ind]
                                    shutil.rmtree(os.path.join(extraction_path, member_root))
                                else:
                                    os.remove(extracted_path)
            break
                

    def harvest_source(self, tar_file, identifier):
        '''
        Get the tar file and create a zip file from the source files
        '''
        zip_file_empty = True
        zip_file = os.path.join(self.config["data_path"], identifier)
        extraction_path = os.path.join(self.config["data_path"], identifier+"_tmp")
        try:
            with tarfile.open(tar_file) as tar_file:
                # this file is a tar file again
                tar_file.extractall(path=extraction_path)
                shutil.make_archive(zip_file, "zip", extraction_path)
        except Exception as e: 
            logging.exception('Could extract/re-archive: ' + tar_file)
        finally:
            # deleting tmp dir
            if os.path.isdir(extraction_path):
                shutil.rmtree(extraction_path)

        if zip_file_empty:
            return None
        else:
            return zip_file

    def get_list_source_files(self):
        list_files = None
        if self.s3_source != None:
            list_files = self.s3_source.get_s3_list("")
        return list_files


    def store_file(self, source, identifier, clean=True):
        file_name = os.path.basename(source)
        collection, prefix, number = _generate_storage_components(identifier)

        if collection == 'arxiv':
            full_number = prefix+"."+number
        else:
            full_number = prefix+number

        if self.s3 is not None:
            try:
                if os.path.isfile(source):
                    dest_path = os.path.join(collection, prefix, full_number)
                    self.s3.upload_file_to_s3(source, dest_path, storage_class='ONEZONE_IA')
            except:
                logging.error("Error writing on S3 bucket")

        elif self.swift is not None:
            # to SWIFT object storage, we can do a bulk upload for all the resources associated to the entry
            try:
                if os.path.isfile(source):
                    dest_path = os.path.join(collection, prefix, full_number)
                    self.swift.upload_file_to_swift(source, dest_path)
            except:
                logging.error("Error writing on SWIFT object storage")

        else:
            # save under local storate indicated by data_path in the config json
            try:
                local_dest_path = os.path.join(self.config["data_path"], collection, prefix, full_number)
                os.makedirs(local_dest_path, exist_ok=True)
                if os.path.isfile(source):
                    shutil.copyfile(source, os.path.join(local_dest_path, file_name))
            except IOError:
                logging.exception("invalid path")    

        # clean stored files
        if clean:
            try:
                if os.path.isfile(source):
                    os.remove(source)
            except IOError:
                logging.exception("temporary file cleaning failed")   


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description = "arXiv source harvester (e.g. latex, bibtex, etc. files)")
    parser.add_argument("--config", default="./config.json", help="path to the config file, default is ./config.json") 
    parser.add_argument("--reset", action="store_true", help="ignore previous processing states and re-init the harvesting process from the beginning") 

    args = parser.parse_args()

    config_path = args.config
    reset = args.reset

    config = _load_config(config_path)

    harvester = ArXivSourceHarvester(config=config)

    if reset:
        harvester.reset()

    start_time = time.time()

    harvester.harvest_sources()

    runtime = round(time.time() - start_time, 3)
    print("runtime: %s seconds " % (runtime))
