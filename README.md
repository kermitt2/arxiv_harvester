# Poor man's harvester for arXiv resources

This modest Python script takes advantage of [arXiv resources hosted by Kaggle](https://www.kaggle.com/Cornell-University/arxiv) to harvest arXiv metadata and PDF, without using the AWS requester paid buckets. 

The harvester performs the following tasks:

* parse the full JSON arXiv metadata file available at Kaggle

* parallel download PDF located at the public access bucket `gs://arxiv-dataset` and store them (also in parallel) on a cloud storage, AWS S3 and Swift OpenStack supported, or on the local file system, or as HuggingFace dataset

* store the metadata of the uploaded article along with the PDF in JSON format

* by default compress everything with gzip

* the files are organized based on their arXiv identifiers (PDF with JSON metadata files), so that direct access to one resource based on its arXiv identifier is straightfoward

* optionally add source files along the PDF and JSON metadata, if archives of LaTeX source can be accessed for the not so poor users

To save storage space, only the most recent available version of the PDF for an article is harvested, not every available versions. 

Resuming interrupted and incremental update are automatically supported. 

In case an article is only available in postcript, it will be converted into PDF too - but it is extremely rare (and usually when it happens the conversion fails because the PostSript is corrupted...). 

## Install 

The tool is supposed to work on a POSIX environment. External call to the following command lines are used: `gzip`, `gunzip` and `ps2pdf`.

First, download the full arXiv metadata JSON file available at [https://www.kaggle.com/Cornell-University/arxiv](https://www.kaggle.com/Cornell-University/arxiv) (1GB compressed). It's actually a JSONL file (one JSON document per line), currently named `arxiv-metadata-oai-snapshot.json.zip`. You can also generate yourself this file with [arxiv-public-dataset OAI harvester](https://github.com/mattbierbaum/arxiv-public-datasets#article-metadata) using the arXiv OAI-PMH service.

Get this github repo:

```sh
git clone https://github.com/kermitt2/arxiv_harvester
cd arxiv_harvester
```

Setup a virtual environment:

```sh
virtualenv --system-site-packages -p python3.8 env
source env/bin/activate
```

Install the dependencies:

```sh
python3 -m pip install -r requirements.txt
```

Finally install the project in editable state:

```sh
python3 -m pip install -e .
```

## Usage 

First check the configuration file:

* set the parameters according to your selected storage (AWS S3, SWIFT OpenStack or local storage), see [below](https://github.com/kermitt2/arxiv_harvester#cloud-storage) for more details, 
* the default `batch_size` for parallel download/upload is `10`, change it as you wish and dare, 
* by default gzip `compression` of files on the target storage is selected. 

```
arXiv harvester

optional arguments:
  -h, --help           show this help message and exit
  --config CONFIG      path to the config file, default is ./config.json
  --reset              ignore previous processing states and re-init the harvesting process from
                       the beginning
  --metadata METADATA  arXiv metadata json file
  --diagnostic         produce a summary of the harvesting
```

For example, to harvest articles from a metadata snapshot file:

```sh
python3 -m arxiv_harvester.harvester --metadata arxiv-metadata-oai-snapshot.json.zip --config config.json
```

To reset an existing harvesting and starts the harvesting again from scratch, add the `--reset` argument:

```sh
python3 -m arxiv_harvester.harvester --metadata arxiv-metadata-oai-snapshot.json.zip --config config.json --reset
```

Note that with `--reset`, no actual stored PDF file is removed - only the harvesting process is reinitialized. 

## Interrupted harvesting / Incremental update

Launching the harvesting command on an interrupted harvesting will resume the harvesting automatically where it stopped. 

If the arXiv metadata file has been updated to a newer version (downloaded from [https://www.kaggle.com/Cornell-University/arxiv](https://www.kaggle.com/Cornell-University/arxiv) or generated with [arxiv-public-dataset OAI harvester](https://github.com/mattbierbaum/arxiv-public-datasets#article-metadata)), launching the harvesting command on the updated metadata file will harvest only the new and updated articles (new most recent PDF version). 

## Resource file organization 

The organization of harvested files permits a direct access to the PDF based on the arxiv identifier. More particularly, the Open Access link given for an arXiv resource by [Unpaywall](https://unpaywall.org/) is enough to create a direct access path. It also avoids storing too many files in the same directory for performance reasons. 

The stored PDF is always the most recent version. There is no need to know what is the exact latest version (an information that we don't have with the Unpaywall arXiv full text links for example). The local metadata file for the article gives the version number of the stored PDF. 

For example, to get access path from the identifiers or Unpaywall OA url:

- post-2007 arXiv identifiers (pattern `arXiv:YYMM.numbervV` or commonly `YYMM.numbervV`): 

    * `1501.00001v1` -> `$root/arXiv/1501/1501.00001/1501.00001.pdf` (most recent version of the PDF), `$root/arXiv/1501/1501.00001/1501.00001.json` (arXiv metadata for the article)
    * Unpaywall link `http://arxiv.org/pdf/1501.00001` -> `$root/arXiv/1501/1501.00001/1501.00001.pdf`, `$root/arXiv/1501/1501.00001/1501.00001.json`

- pre-2007 arXiv identifiers (pattern `archive.subject_call/YYMMnumber`):

    * `quant-ph/0602109` -> `$root/quant-ph/0602/0602109/0602109.pdf` (most recent version of the PDF), `$root/quant-ph/0602/0602109/0602109.json` (arXiv metadata for the article)

    * Unpaywall link `https://arxiv.org/pdf/quant-ph/0602109` -> `$root/quant-ph/0602/0602109/0602109.pdf`, `$root/quant-ph/0602/0602109/0602109.json`

If the `compression` option is set to `True` in the configuration file `config.json`, all the resources have an additional `.gz` extension.

`$root` in the above examples should be adapted to the storage of choice, as configured in the configuration file `config.json`. For instance with AWS S3: `https://bucket_name.s3.amazonaws.com/arXiv/1501/1501.00001/1501.00001.pdf` (if access rights are appropriate). The same applies to a SWIFT object storage based on the container name indicated in the config file. 

## Cloud storage

The default storage is local file system storage, with the data path as indicated in the config file (`data_path` field). 

It is possible to storage on the cloud by setting one cloud storage (and only one!). 

### AWS S3 configuration

For a local storage, just indicate the path where to store the PDF with the parameter `data_path` in the configuration file `config.json`.

The configuration for a S3 storage uses the following parameters:

```json
{
    "aws_access_key_id": "",
    "aws_secret_access_key": "",
    "bucket_name": "",
    "region": ""
}
```

If you are not using a S3 storage, remove these keys or leave these values empty. 

The configuration for a SWIFT object storage uses the following parameters:

```json
{
    "swift": {},
    "swift_container": ""
}
```

### OpenStack SWIFT configuration

If you are not using a SWIFT storage, remove these keys or leave these above values empty.

The `"swift"` key will contain the account and authentication information, typically via Keystone, something like this: 

```json
{
    "swift": {
        "auth_version": "3",
        "auth_url": "https://auth......./v3",
        "os_username": "user-007",
        "os_password": "1234",
        "os_user_domain_name": "Default",
        "os_project_domain_name": "Default",
        "os_project_name": "myProjectName",
        "os_project_id": "myProjectID",
        "os_region_name": "NorthPole",
        "os_auth_url": "https://auth......./v3"
    },
    "swift_container": "my_arxiv_harvesting"
}
```

### HuggingFace dataset

This is currently working as of June 2023, but the generous HuggingFace data space for free might change in the future. The repo identifier of the HuggingFace dataset need to be specified in the `config.json` file (`hf_repo_id`). The **secret** HuggingFace access token can be specified as well in the config file, or as environment variable (`HUGGINGFACE_TOKEN`), or it is also possible to first login with the HuggingFace CLI before running the script. 

```json
{
    "hf_repo_id": "",
    "HUGGINGFACE_TOKEN": ""
}
```

## Adding LaTeX sources

Source files (LaTeX sources) are not available via the [Kaggle dataset](https://www.kaggle.com/Cornell-University/arxiv/discussion/185299) and thus not directly via this modest harvester. However, the LaTeX source files are available via [AWS S3 Bulk Source File Access](https://arxiv.org/help/bulk_data_s3#bulk-source-file-access). Assuming the source file are available on a S3 bucket specified in the configuration file `config.json`, adding the source file can be done as follow: 

```
python3 -m arxiv_harvester.harvester_sources --config config.json
```

The LaTeX source archive files will be downloaded one by one and re-packaged at publication-level. These document-level LaTeX source files (as a zip archives, one per document) are added in the corresponding arXiv item directory, e.g.: `$root/quant-ph/0602/0602109/0602109.zip` or `$root/arXiv/1501/1501.00001/1501.00001.zip`.

Similarly as before, relaunching the command line will resume the harvesting process if interrupted. Similarly as before, using the `--reset` argument will re-initialize entirely the process, erasing possible files under the `data_path` and re-starting the process from the beginning. 

## Limitation

There are 44 articles only available in HTML format. These articles will not be harvested. 

## Acknowledgements

Kaggle arXiv dataset relies on [arxiv-public-datasets](https://github.com/mattbierbaum/arxiv-public-datasets):  

Clement, C. B., Bierbaum, M., O'Keeffe, K. P., & Alemi, A. A. (2019). On the Use of ArXiv as a Dataset. arXiv preprint [arXiv:1905.00075](https://arxiv.org/abs/1905.00075).

## License and contact

This modest tool is distributed under [Apache 2.0 license](http://www.apache.org/licenses/LICENSE-2.0). The dependencies used in the project are either themselves also distributed under Apache 2.0 license or distributed under a compatible license. 

If you contribute to this Open Source project, you agree to share your contribution following this license. 

Kaggle dataset arXiv Metadata is distributed under [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0) license. Note that most articles on arXiv are submitted with the default arXiv license, which does usually not allow redistribution. See [here](https://arxiv.org/help/api/tou#things-that-you-can-and-should-do) about the possible usage of the harvested PDF.

Main author and contact: Patrice Lopez (<patrice.lopez@science-miner.com>)
