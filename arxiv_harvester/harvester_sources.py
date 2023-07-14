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

from arxiv_harvester.harvester import _load_config, _generate_storage_components

import pickle
import lmdb

import tarfile
from zipfile import ZipFile

# init LMDB
map_size = 200 * 1024 * 1024 * 1024 

class ArXivSourceHarvester(object):

    def __init__(self, config):
        self.config = config

        self._init_lmdb()

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

    def _init_lmdb(self):
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

    def harvest_sources(self, file_list=None):
        # we download archive one after the other from the S3 bucket
        # we extract the resources of the current archive, and move the resources according to the 
        # arxiv identifier at the right place
        # when done the archive is deleted or stored, and we download the next one

        list_files = self.set_list_files(file_list=file_list)
        print("Number of source archive files:", str(len(list_files)))

        pbar = tqdm(total=len(list_files))
        for file in list_files:
            # already processed? 
            with self.env_source.begin() as txn:
                local_object = txn.get(file.encode(encoding='UTF-8'))
                if local_object != None:
                    pbar.update(1)
                    continue

            # download the archive file, these are tar files
            dest_path = os.path.join(self.config["data_path"], file)
            dest_path = self.s3_source.download_file(file, dest_path)
            if dest_path == None:
                logging.error("S3 download failed for " + file)
            else:
                with tarfile.open(dest_path) as tar:
                    nb_files = 0

                    #for member in tqdm(tar.getmembers(), total=len(tar.getmembers())):
                    for member in tar.getmembers():
                        # get gzip files and ignore PDF files, the gzip files are actually tar gzip files with the sources inside
                        if not member.name.endswith(".gz"):
                            continue
                        # we have to put the identifier into a correct format (as it is at this stage simply the file name)
                        identifier = os.path.basename(member.name)
                        identifier = identifier.replace(".gz", "")
                        #print("identifier:", identifier)
                        
                        extraction_path = self.config["data_path"]
                        extracted_path = None
                        try:
                            tar.extract(member=member, path=extraction_path)
                            extracted_path = os.path.join(extraction_path, member.name)
                        finally:
                            # withdrawn papers are not tar files (just gzip) and starts with "%auto-ignore"
                            # single tex file article are not tar files (just gzip), they normally starts with \documentclass
                            # or \documentstyle
                            is_auto_ignore = False
                            is_single_latex = False
                            try:
                                with gzip.open(extracted_path,'rt') as f:
                                    for line in f:
                                        line = line.strip()
                                        if line.startswith("%auto-ignore"):
                                            is_auto_ignore = True
                                        elif line.startswith("\\document"):
                                            is_single_latex = True
                                        break
                            except:
                                logging.debug("Error opening extracted file: " + extracted_path)

                            if is_auto_ignore:
                                # skip withdrawn file
                                continue

                            # otherwise we have likely gzip tar archive
                            if not is_auto_ignore and not is_single_latex: 
                                try:
                                    local_zip_file = self.harvest_source(extracted_path, identifier)
                                    #print(local_zip_file)

                                    # upload zip file if not empty
                                    if local_zip_file != None:
                                        zip_dest_path = os.path.join(self.config["data_path"], identifier + ".zip")
                                        identifier = _format_identifier(identifier)
                                        #print("identifier (reformatted):", identifier)
                                        self.store_file(zip_dest_path, identifier)
                                    else:
                                        # ok it was single latex file too
                                        is_single_latex = True
                                except:
                                    logging.debug("Error opening extracted file: " + extracted_path)
                            
                            if is_single_latex:
                                # not tar, but gzip plain latex file to be zipped
                                extracted_path_tmp = None
                                try:
                                    zip_file = os.path.join(self.config["data_path"], identifier)
                                    extracted_path_tmp = os.path.join(self.config["data_path"], identifier+"_tmp")
                                    os.mkdir(extracted_path_tmp)
                                    with gzip.open(extracted_path, 'rb') as f_in:
                                        with open(os.path.join(extracted_path_tmp, identifier+".tex"), 'wb') as f_out:
                                            shutil.copyfileobj(f_in, f_out)

                                    shutil.make_archive(zip_file, "zip", extracted_path_tmp)
                                    zip_dest_path = os.path.join(self.config["data_path"], identifier + ".zip")
                                    identifier = _format_identifier(identifier)
                                    self.store_file(zip_dest_path, identifier)
                                finally:
                                    if extracted_path_tmp != None and os.path.isdir(extracted_path_tmp):
                                        shutil.rmtree(extracted_path_tmp)

                            if extracted_path != None and os.path.isfile(extracted_path):
                                ind = member.name.find("/")
                                if ind != -1:
                                    member_root = member.name[:ind]
                                    shutil.rmtree(os.path.join(extraction_path, member_root))
                                else:
                                    os.remove(extracted_path)

                        nb_files += 1

                # update lmdb to keep track of the process
                with self.env_source.begin(write=True) as txn:
                    txn.put(file.encode(encoding='UTF-8'), str(nb_files).encode(encoding='UTF-8'))

                # delete the large locally downloaded arxiv
                if dest_path != None and os.path.isfile(dest_path):
                    os.remove(dest_path)

            pbar.update(1)
            #break
        pbar.close()

    def harvest_source(self, tar_file, identifier):
        '''
        Get the tar file and create a zip file from the source files
        '''
        zip_file_empty = True
        zip_file = os.path.join(self.config["data_path"], identifier)
        extraction_path = os.path.join(self.config["data_path"], identifier+"_tmp")
        try:
            # normally compression is handled transparently when opening the tar (according to tarfile documentation)
            # but we can force "r:gz" as we are sure about the encoding
            with tarfile.open(tar_file, "r") as the_tar_file:
                # this file is a tar file again
                the_tar_file.extractall(path=extraction_path)
                shutil.make_archive(zip_file, "zip", extraction_path)
                zip_file_empty = False
        except Exception as e: 
            logging.debug('Could extract/re-archive: ' + tar_file)
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

        if not os.path.isfile(source):
            logging.error("no valid file to store: " + source)
            return

        #print("store_file:", source, identifier)
        
        original_file_name = os.path.basename(source)
        collection, prefix, number = _generate_storage_components(identifier)

        if collection == 'arxiv':
            full_number = prefix+"."+number
        else:
            full_number = prefix+number

        # rename source file, e.g. quant-ph0001001.zip -> 0001001.zip
        original_source = source
        file_name = original_file_name
        if original_file_name[0].isdigit():
            source = os.path.join(os.path.dirname(source), original_file_name)
        else:
            new_file_name = ""
            for i in range(0, len(original_file_name)):
                c = original_file_name[i]
                if (c.isdigit()):
                    new_file_name += original_file_name[i:]
                    break
            source = os.path.join(os.path.dirname(source), new_file_name)
            file_name = new_file_name

        if original_source != source:
            shutil.copyfile(original_source, source)

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
                if original_source != source:
                    if os.path.isfile(original_source):
                        os.remove(original_source)
                if os.path.isfile(source):
                    os.remove(source)
            except IOError:
                logging.exception("temporary file cleaning failed")   

    def reset(self):
        """
        Remove the local lmdb keeping track of the state of advancement of the harvesting and
        of the failed entries
        """
        # close environments
        self.env_source.close()

        envFilePath = os.path.join(self.config["data_path"], 'sources')
        shutil.rmtree(envFilePath)

        # re-init the environments
        self._init_lmdb()

    def set_list_files(self, file_list):
        list_files = []

        if file_list == None or not os.path.isfile(file_list):
            if os.path.isfile(os.path.join(self.config["data_path"], "list_source_files.txt")):
                with open(os.path.join(self.config["data_path"], "list_source_files.txt"), "r") as the_file:
                    lines = the_file.readlines()
                    # Strips the newline character
                    for line in lines:
                        list_files.append(line.strip())
            else:
                list_files = self.get_list_source_files()
                with open(os.path.join(self.config["data_path"], "list_source_files.txt"), "w") as the_file:
                    for list_file in list_files:
                        the_file.write(list_file)
                        the_file.write("\n")
        else:
            with open(file_list, "r") as the_file:
                lines = the_file.readlines()
                # Strips the newline character
                for line in lines:
                    list_files.append(line.strip())

        return list_files

    def diagnostic(self, file_list=None):
        '''
        Basic information about the state of processing (large tar archives from arXiv sources),
        and number of individual documents covered
        '''

        list_files = self.set_list_files(file_list=file_list)

        with self.env_source.begin(write=False) as txn:
            nb_archives = txn.stat()['entries']
            print("number of fully processed arxiv source archives:", nb_archives, "out of", str(len(list_files)), "archives")

        # sum the number of individual files in each entry
        total_files = 0
        with self.env_source.begin(write=False) as txn:
            cursor = txn.cursor()
            for key, value in cursor:
                if txn.get(key) is None:
                    continue
                number_string = value.decode(encoding='UTF-8')
                number = int(number_string)
                if number != None:
                    total_files += number

            print("number of processed individual arxiv source archives:", total_files)


def _format_identifier(identifier):
    '''
    Re-format a source file name into a usual arXiv identifier
    '''

    # 2208.00127 -> 2208.00127
    if identifier[0].isdigit():
        # normally nothing to do
        return identifier

    # astro-ph0001001 -> astro-ph/0001001
    new_identifer = ""
    for i in range(0, len(identifier)):
        c = identifier[i]
        if (c.isdigit()):
            new_identifer += "/" + c
            new_identifer += identifier[i+1:]
            break
        else:
            new_identifer += c
    return new_identifer

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description = "arXiv source harvester (e.g. latex, bibtex, etc. files)")
    parser.add_argument("--config", default="./config.json", help="path to the config file, default is ./config.json") 
    parser.add_argument("--reset", action="store_true", help="ignore previous processing states and re-init the harvesting process from the beginning") 
    parser.add_argument("--file-list", default=None, help="list of arXiv source archive files to process, default is to process all available on arxiv S3") 
    parser.add_argument("--diagnostic", action="store_true", help="produce a summary of the source harvesting") 

    args = parser.parse_args()

    config_path = args.config
    file_list = args.file_list
    reset = args.reset
    diagnostic = args.diagnostic

    config = _load_config(config_path)

    harvester = ArXivSourceHarvester(config=config)

    if reset:
        if input("\nYou asked to reset the existing harvesting, this will reinitialize the harvesting from the beginning... are you sure? (y/n) ") == "y":
            harvester.reset()
        else:
            print("skipping reset...")

    start_time = time.time()

    if diagnostic:
        harvester.diagnostic()
    else:
        harvester.harvest_sources(file_list=file_list)
        harvester.diagnostic(file_list=file_list)

    runtime = round(time.time() - start_time, 3)
    print("runtime: %s seconds " % (runtime))
