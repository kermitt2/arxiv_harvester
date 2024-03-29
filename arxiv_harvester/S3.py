import os
from boto3 import client

# logging
import logging
import logging.handlers
logging.basicConfig(filename='harvester.log', filemode='w', level=logging.ERROR)

'''
Note: we probably should manage retry
'''

class S3(object):
    
    def __init__(self, config):
        self.config = config
        if self.config['region'] is not None:
            region = self.config['region']
        else:
            region = "us-west-2"
        self.bucket_name = self.config['bucket_name']

        if 'aws_end_point' in self.config and len(self.config['aws_end_point'])>1:
            # for non-AWS S3 compatible storage, e.g. OVHCloud
            end_point = self.config['aws_end_point']
            self.conn = client('s3', 
                            endpoint_url=end_point,
                            region_name=region, 
                            aws_access_key_id=self.config['aws_access_key_id'],
                            aws_secret_access_key=self.config['aws_secret_access_key'])
        else:
            # default AWS
            self.conn = client('s3', 
                            region_name=region, 
                            aws_access_key_id=self.config['aws_access_key_id'],
                            aws_secret_access_key=self.config['aws_secret_access_key'])

    def upload_file_to_s3(self, file_path, dest_path=None, storage_class='STANDARD_IA'):
        """
        Upload the given file to s3 using a managed uploader, which will split up large
        files automatically and upload parts in parallel.
        By default, files are stored with the class standard infrequent access. 
        Possible storage classes are: STANDARD, STANDARD_IA, REDUCED_REDUNDANCY or ONEZONE_IA
        """
        s3_client = self.conn
        file_name = file_path.split('/')[-1]
        if dest_path:
            if dest_path.endswith("/"):
                full_path = dest_path + file_name
            else:
                full_path = dest_path + "/" + file_name
        else:
            full_path = file_name
        try:
            s3_client.upload_file(file_path, self.bucket_name, full_path, ExtraArgs={"Metadata": {"StorageClass": storage_class}})
        except Exception as e: 
            logging.exception('Could not upload file ' + file_path)    

    def upload_object(self, body, s3_key, storage_class='STANDARD_IA'):
        """
        Upload object to s3 key.
        By default, files are stored with the class standard infrequent access. 
        Possible storage classes are: STANDARD, STANDARD_IA, REDUCED_REDUNDANCY or ONEZONE_IA
        """
        s3_client = self.conn
        return s3_client.put_object(Body=body, Key=s3_key, ExtraArgs={"Metadata": {"StorageClass": storage_class}})

    def download_file(self, file_path, dest_path):
        """
        Download a file given a S3 path and returns the download file path.
        """
        s3_client = self.conn
        file_name = os.path.basename(file_path)
        try:
            s3_client.download_file(self.bucket_name, file_path, dest_path)
        except Exception as e: 
            logging.exception('Could not download file: ' + file_path)
            return None
        
        return dest_path

    def get_s3_list(self, dir_name):
        """
        Return all contents of a given dir in s3.
        Goes through the pagination to obtain all file names, so possibly super inefficient
        """
        dir_name = dir_name.split('tmp/')[-1]
        paginator = self.conn.get_paginator('list_objects')
        s3_results = paginator.paginate(
            Bucket=self.bucket_name,
            Prefix=dir_name,
            PaginationConfig={'PageSize': 1000}
        )
        bucket_object_list = []
        for page in s3_results:
            if "Contents" in page:
                for key in page["Contents"]:
                    s3_file_name = key['Key'].split('/')[-1]
                    bucket_object_list.append(s3_file_name)
        return bucket_object_list

    def remove_file(self, file_path):
        """
        Remove an existing file on the current S3 bucket
        """ 
        s3_client = self.conn
        try:
            s3_client.delete_object(Bucket=self.bucket_name, Key=file_path)
        except:
            logging.error('Could not delete file: ' + file_path)
            return False
        return True

    def remove_all_files(self):
        """
        Empty all the content of the current bucket under the provided path
        """
        try:
            bucket = self.conn.Bucket(self.bucket_name)
            bucket.objects.all().delete()
        except Exception as e:
            logging.exception("Could not empty the bucket " + self.bucket_name)
