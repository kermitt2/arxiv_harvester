# Poor man simple harvester for arXiv resources

This modest Python script takes advantage of [arXiv resources hosted by Kaggle](https://www.kaggle.com/Cornell-University/arxiv) to harvest arXiv metadata and PDF, without using the AWS requester paid buckets. 

The harvester performs the following tasks:

* parse the full JSON arXiv metadata file available at Kaggle

* parallel download PDF located at the public access bucket `gs://arxiv-dataset` and store them (also in parallel) on a cloud storage, AWS S3 and Swift OpenStack supported, or on the local file system

* store the metadata of the uploaded article along with the PDF in JSON format

To save storage space, only the most recent available version of the PDF for an article is harvested, not every available versions. 

Resuming interrupted and incremental update are automatically supported. 

In case an article is only available in postcript, it will be converted into PDF too - but it is extremely rare (and usually when it happens the conversion fails because the PostSript is corrupted...). 

## Install 

The tool is supposed to work on a POSIX environment. External call to the following command lines are used: `gzip`, `gunzip` and `ps2pdf`.

First, download the full arXiv metadata JSON file available at [https://www.kaggle.com/Cornell-University/arxiv](https://www.kaggle.com/Cornell-University/arxiv) (1TB compressed). It's actually a JSONL file (one JSON document per line), currently named `arxiv-metadata-oai-snapshot.json.zip`. You can also generate yourself this file with [arxiv-public-dataset OAI harvester](https://github.com/mattbierbaum/arxiv-public-datasets#article-metadata) using the arXiv OAI-PMH service.

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
pip3 install -r requirements.txt
```

Finally install the project in editable state:

```sh
pip3 install -e .
```

## Usage 

First check the configuration file:

* set the parameters according to your selected storage (AWS S3, SWIFT OepnStack or local storage), see [below](https://github.com/kermitt2/arxiv_harvester#aws-s3-and-swift-configuration) for more details, 
* the default `batch_size` for parallel download/upload is `10`, change it as you wish, 
* by default gzip `compression` of files on the target storage is selected. 

```
arXiv harvester

optional arguments:
  -h, --help           show this help message and exit
  --config CONFIG      path to the config file, default is ./config.json
  --reset              ignore previous processing states, clear the existing storage and re-init
                       the harvesting process from the beginning
  --metadata METADATA  arXiv metadata json file
```

For example, to harvest articles from a metadata snapshot file:

```sh
python3 arxiv_harvester/harvester.py --metadata arxiv-metadata-oai-snapshot.json.zip --config config.json
```

To reset an existing harvesting and starts the harvesting again from scratch, add the `--reset` argument:

```sh
python3 arxiv_harvester/harvester.py --metadata arxiv-metadata-oai-snapshot.json.zip --config config.json --reset
```

## Interrupted harvesting / Incremental update

Launching the harvesting command on an interrupted harvesting will resume the harvesting automatically where it stopped. 

If the arXiv metadata file has been updated to a newer version (downloaded from [https://www.kaggle.com/Cornell-University/arxiv](https://www.kaggle.com/Cornell-University/arxiv) or generated with [arxiv-public-dataset OAI harvester](https://github.com/mattbierbaum/arxiv-public-datasets#article-metadata), launching the harvesting command on the updated metadata file will harvest only the new and updated (new latest version) articles. 

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

## AWS S3 and SWIFT configuration

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

## Limitations

Source files (LaTeX sources) are not available via the [Kaggle dataset](https://www.kaggle.com/Cornell-University/arxiv/discussion/185299) and thus via this modest harvester. The LaTeX source files are available via [AWS S3 Bulk Source File Access](https://arxiv.org/help/bulk_data_s3#bulk-source-file-access).

There are 44 articles only available in HTML format. These articles will not be harvested. 

## Acknowledgements

Kaggle arXiv dataset relies on [arxiv-public-datasets](https://github.com/mattbierbaum/arxiv-public-datasets):  

Clement, C. B., Bierbaum, M., O'Keeffe, K. P., & Alemi, A. A. (2019). On the Use of ArXiv as a Dataset. arXiv preprint [arXiv:1905.00075](https://arxiv.org/abs/1905.00075).

## License and contact

This modest tool is distributed under [Apache 2.0 license](http://www.apache.org/licenses/LICENSE-2.0). The dependencies used in the project are either themselves also distributed under Apache 2.0 license or distributed under a compatible license. 

If you contribute to this Open Source project, you agree to share your contribution following this license. 

Kaggle dataset arXiv Metadata is distributed under [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0) license. Note that most articles on arXiv are submitted with the default arXiv license, which does usually not allow redistribution. See [here](https://arxiv.org/help/api/tou#things-that-you-can-and-should-do) about the possible usage of the harvested PDF.

Main author and contact: Patrice Lopez (<patrice.lopez@science-miner.com>)
