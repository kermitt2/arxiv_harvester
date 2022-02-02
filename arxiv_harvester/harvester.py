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
import weasyprint
from zipfile import ZipFile

# support for S3
import S3

# support for SWIFT object storage
import swift

#from google.cloud import storage
import urllib3

# logging
import logging
import logging.handlers
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logging.getLogger("urllib3").setLevel(logging.ERROR)
logging.getLogger("keystoneclient").setLevel(logging.ERROR)
logging.getLogger("swiftclient").setLevel(logging.ERROR)

# public access base for google cloud storage
gcs_base = "http://storage.googleapis.com/arxiv-dataset/arxiv/"

import pickle
import lmdb

# init LMDB
map_size = 200 * 1024 * 1024 * 1024 

class ArXivHarvester(object):

    def __init__(self, config):
        self.config = config

        self._init_lmdb()

        self.s3 = None
        if "bucket_name" in self.config and len(self.config["bucket_name"].strip()) > 0:
            self.s3 = S3.S3(self.config)

        self.swift = None
        if "swift" in self.config and len(self.config["swift"])>0 and "swift_container" in self.config and len(self.config["swift_container"])>0:
            self.swift = swift.Swift(self.config)

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
        envFilePath = os.path.join(self.config["data_path"], 'entries')
        self.env = lmdb.open(envFilePath, map_size=map_size)

    def harvest(self, metadata_file):
        if metadata_file is None or not os.path.isfile(metadata_file):
            raise("the provided metadata file is not valid")

        if not metadata_file.endswith(".zip") and not metadata_file.endswith(".json.gz") and not metadata_file.endswith(".json"):
            raise("the metadata file must be a jsonl file, or a zipped or gziped jsonl file")

        file_in = _get_json_file_reader(metadata_file, 'rb')
        
         # check the overall number of entries based on the line number
        print("\ncalculating number of entries...")
        count = 0
        while 1:
            buffer = file_in.read(8192*1024)
            if not buffer: break
            count += buffer.count(b'\n')
        print("total entries found: " + str(count) + "\n")
        file_in.close()

        # iterate through the jsonl file
        file_in = _get_json_file_reader(metadata_file, 'r')
        for line in tqdm(file_in, total=count):
            entry = json.loads(line)
            if 'id' not in entry:
                print("entry without arxiv id, skipping...")
                continue
            arxiv_id = entry['id']
            latest_version = _get_latest_version(entry)
            # google cloud public access: gs://arxiv-dataset/arxiv/arxiv/pdf/0906/0906.5594v2.pdf
            # public web access, preferred: http://storage.googleapis.com/arxiv-dataset/arxiv/

            # check if document and version are already processed
            with self.env.begin() as txn:
                local_object = txn.get(arxiv_id.encode(encoding='UTF-8'))
                if local_object != None:
                    local_entry = _deserialize_pickle(local_object)
                    if local_entry != None:
                        if "version" in local_entry and local_entry["version"] == latest_version:
                            continue

            collection, prefix, number = generate_storage_components(arxiv_id)
            if collection == 'arxiv':
                full_number = prefix+"."+number
            else:
                full_number = prefix+number
            pdf_location = gcs_base + collection + '/pdf/' + prefix + "/" + full_number + latest_version + ".pdf"

            # temporary place to download the file
            destination_pdf = os.path.join(self.config["data_path"], full_number + ".pdf")
            # destination file nanme can change if compression is true in config
            print(pdf_location)
            destination_pdf = self.download_file(pdf_location, destination_pdf, compression=self.config["compression"])

            if destination_pdf is None:
                # if not found, look for a ps file
                ps_location = gcs_base + collection + '/ps/' + prefix + "/" + full_number + latest_version + ".ps.gz"
                destination_ps = os.path.join(self.config["data_path"], full_number + ".ps.gz")
                destination_ps = self.download_file(ps_location, destination_ps, compression=False)

                if destination_ps is None:
                    # if still not found, they are 44 articles in html only 
                    print("Full text article not found for", arxiv_id, "it might be available in html only")
                else:
                    # for convenience, convert .ps.gz into PDF
                    destination_pdf = os.path.join(self.config["data_path"], arxiv_id + ".pdf")
                    # first gunzip the ps file
                    subprocess.check_call(['gunzip', '-f', destination_ps])
                    destination_ps = destination_ps.replace(".ps.gz", ".ps")
                    subprocess.check_call(['ps2pdf', destination_ps, destination_pdf])
                    # clean ps file
                    try:
                        if os.path.isfile(destination_ps):
                            os.remove(destination_ps)
                    except IOError:
                        logging.exception("temporary ps file cleaning failed")  

                    if destination_pdf is not None:
                        if self.config["compression"]:
                            compression_suffix = ".gz"
                            try:
                                if os.path.isfile(destination_pdf):
                                    subprocess.check_call(['gzip', '-f', destination_pdf])
                                    destination_pdf += compression_suffix
                            except:
                                logging.error("Error compressing resource files for " + destination_pdf)   

            if destination_pdf is not None:
                # store the pdf file in the selected storage
                self.store_file(destination_pdf, arxiv_id, latest_version)

                # update advancement status map
                profile = {}
                profile['id'] = arxiv_id
                profile['version'] = latest_version
                if 'doi' in entry and entry['doi'] != None:
                    profile['doi'] = entry['doi']
                with self.env.begin(write=True) as txn:
                    txn.put(arxiv_id.encode(encoding='UTF-8'), _serialize_pickle(profile))

            # store the metadata file
            destination_json = os.path.join(self.config["data_path"], arxiv_id+".json")
            with open(destination_json, 'w', encoding='utf-8') as outfile:
                json.dump(entry, outfile, ensure_ascii=False)
            if self.config["compression"]:
                compression_suffix = ".gz"
                try:
                    if os.path.isfile(destination_json):
                        subprocess.check_call(['gzip', '-f', destination_json])
                        destination_json += compression_suffix
                except:
                    logging.error("Error compressing resource files for " + destination_json)   
            self.store_file(destination_json, arxiv_id, latest_version)

        dump_destination = os.path.join(self.config["data_path"], "arxiv_list.json")
        self.dump_map(dump_destination)


    def download_file(self, source_url, destination, compression=False):
        HEADERS = {"""User-Agent""": _get_random_user_agent()}
        result = "fail"
        try:
            file_data = requests.get(source_url, allow_redirects=True, headers=HEADERS, verify=False, timeout=30)
            if file_data.status_code == 200:
                with open(destination, 'wb') as f_out:
                    f_out.write(file_data.content)
                result = "success"
        except Exception:
            logging.exception("Download failed for {0} with requests".format(source_url))

        if result != "success":
            return None

        if compression:
            compression_suffix = ".gz"
            try:
                if os.path.isfile(destination):
                    subprocess.check_call(['gzip', '-f', destination])
                    destination += compression_suffix
            except:
                logging.error("Error compressing resource files for " + destination)   

        return destination

    def store_file(self, source, identifier, version, clean=True):
        file_name = os.path.basename(source)
        collection, prefix, number = generate_storage_components(identifier)

        if collection == 'arxiv':
            full_number = prefix+"."+number
        else:
            full_number = prefix+number

        if self.s3 is not None:
            try:
                if os.path.isfile(source):
                    dest_path = os.path.join(collection, prefix, full_number, file_name)
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

    def dump_map(self, destination):
        # init lmdb transactions
        with open(destination,'w') as file_out:
            with self.env.begin(write=True) as txn:
                cursor = txn.cursor()
                for key, value in cursor:
                    if txn.get(key) is None:
                        continue
                    map_entry = _deserialize_pickle(txn.get(key))
                    json_local_entry = json.dumps(map_entry)
                    file_out.write(json_local_entry)
                    file_out.write("\n")

        if self.config["compression"]:
            subprocess.check_call(['gzip', '-f', destination])
            destination += ".gz"

        # store dump 
        file_name = os.path.basename(destination)
        if self.s3 is not None:
            try:
                if os.path.isfile(destination):
                    self.s3.upload_file_to_s3(destination, file_name, storage_class='ONEZONE_IA')
            except:
                logging.error("Error writing on S3 bucket")

        elif self.swift is not None:
            # to SWIFT object storage, we can do a bulk upload for all the resources associated to the entry
            try:
                if os.path.isfile(destination):
                    self.swift.upload_file_to_swift(destination, file_name)
            except:
                logging.error("Error writing on SWIFT object storage")

        return destination

    def diagnostic(self):
        with self.env.begin(write=True) as txn:
            nb_total = txn.stat()['entries']
            print("\nnumber of successfully harvested entries:", nb_total)

    def reset(self):
        """
        Remove the local lmdb keeping track of the state of advancement of the harvesting and
        of the failed entries
        """
        # close environments
        self.env.close()

        envFilePath = os.path.join(self.config["data_path"], 'entries')
        shutil.rmtree(envFilePath)

        # re-init the environments
        self._init_lmdb()

def _get_json_file_reader(filename, mode):
    file_in = None
    if filename.endswith(".zip"):
        with ZipFile(filename, 'r') as zipObj:
           list_filenames = zipObj.namelist()
           for local_filename in list_filenames:
                if local_filename.endswith('.json'):
                    file_in = zipObj.open(local_filename, mode='r')
                    break
    elif filename.endswith(".gz"):
        file_in = gzip.open(filename, mode)
    else: 
        # uncompressed file
        file_in = open(filename, mode)
    return file_in

def _get_latest_version(json_entry):
    latest_version = None
    if "versions" in json_entry:
        for version in json_entry["versions"]:
            # time indicated by "created" attribute
            latest_version = version["version"]

    if latest_version == None:
        # default value
        latest_version = "v1" 
    return latest_version

def generate_storage_components(identifier):
    '''
    Convert an arxiv identifier into components for storage path purposes 
    
    post-2007 identifier:
    arXiv:YYMM.numbervV -> arxiv YYMM number
    arXiv:1501.00001v1 -> arXiv 1501 00001

    pre-2007 identifiers:
    archive.subject_call/YYMMnumber -> archive YYMM number
    math.GT/0309136 -> math 0309 136

    return: collection, prefix, number 
    e.g. arxiv 1501 00001

    '''

    if identifier is None or len(identifier) == 0:
        return None, None

    collection = None
    prefix =None
    number = None

    if identifier[0].isdigit():
        # we have a post-2007 identifier
        collection = "arxiv"
        ind = identifier.find(".")
        if ind == -1:
            prefix = identifier[:4]
        else:
            prefix = identifier[:ind]
        endpos = identifier.find("v")
        if endpos == -1:
            endpos = len(identifier)
        number = identifier[5:endpos]
    else:
        # we have a pre-2007 identifier
        ind = identifier.find("/")
        if ind != -1:
            collection = identifier[:ind]
            prefix = identifier[ind+1:ind+5]
            number = identifier[ind+5:]

    return collection, prefix, number

def _serialize_pickle(a):
    return pickle.dumps(a)

def _deserialize_pickle(serialized):
    return pickle.loads(serialized)

def _load_config(path='./config.json'):
    """
    Load the json configuration 
    """
    config_json = open(path).read()
    return json.loads(config_json)

def _get_random_user_agent():
    '''
    This is a simple random/rotating user agent covering different devices and web clients/browsers
    Note: rotating the user agent without rotating the IP address (via proxies) might not be a good idea if the same server
    is harvested - but in our case we are harvesting a large variety of different Open Access servers
    '''
    user_agents = ["Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:81.0) Gecko/20100101 Firefox/81.0",
                   "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/94.0.4606.81 Safari/537.36",
                   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.159 Safari/537.36"]
    weights = [0.2, 0.3, 0.5]
    user_agent = choices(user_agents, weights=weights, k=1)

    return user_agent[0]

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description = "arXiv harvester")
    parser.add_argument("--config", default="./config.json", help="path to the config file, default is ./config.json") 
    parser.add_argument("--reset", action="store_true", help="ignore previous processing states, clear the existing storage and re-init the harvesting process from the beginning") 
    parser.add_argument("--metadata", help="arXiv metadata json file") 

    args = parser.parse_args()

    metadata = args.metadata
    config_path = args.config
    reset = args.reset

    config = _load_config(config_path)

    harvester = ArXivHarvester(config=config)

    if reset:
        harvester.reset()

    start_time = time.time()

    if metadata is not None: 
        harvester.harvest(metadata)
        harvester.diagnostic()

    runtime = round(time.time() - start_time, 3)
    print("runtime: %s seconds " % (runtime))
