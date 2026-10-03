"""Immutable manifest pinning the content of reviewed generated Shopify product docs."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re

NAME = re.compile(r'products/product-[a-z0-9][a-z0-9-]*\.md\Z')


def regular(path):
    if any(p.is_symlink() for p in (path, *path.parents)) or not path.is_file():
        raise ValueError('Manifest source must have regular non-symlink boundaries')


def load_manifest(path, expected_hash):
    regular(path)
    if path.stat().st_size > 4_000_000:
        raise ValueError('Oversized catalog manifest')
    raw=path.read_bytes()
    if not re.fullmatch('[a-f0-9]{64}', expected_hash or '') or hashlib.sha256(raw).hexdigest()!=expected_hash:
        raise ValueError('Catalog manifest hash does not match reviewed snapshot')
    value=json.loads(raw)
    if set(value)!={'schema','source','generator_sha256','directory','files'} or value['schema']!=2 or value['source']!='shopify-sync-products':
        raise ValueError('Unsupported catalog provenance')
    files=value['files']
    if not isinstance(files,dict) or not 1<=len(files)<=50000 or any(not NAME.fullmatch(x) or not re.fullmatch('[a-f0-9]{64}',str(h)) for x,h in files.items()):
        raise ValueError('Catalog manifest must map product filenames to content hashes only')
    if not re.fullmatch('[a-f0-9]{64}',value['generator_sha256']):
        raise ValueError('Missing reviewed generator digest')
    directory=Path(value['directory'])
    if not directory.is_absolute():
        raise ValueError('Catalog manifest needs the absolute product directory')
    for name,expected in files.items():
        path=directory/PurePosixPath(name).name
        regular(path)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
            raise ValueError('Product content differs from the reviewed snapshot')
    return set(files)


def snapshot(directory,generator,expected_generator_hash,output):
    regular(generator)
    digest=hashlib.sha256(generator.read_bytes()).hexdigest()
    if digest!=expected_generator_hash:
        raise ValueError('Product generator differs from reviewed source')
    if not directory.is_dir() or any(p.is_symlink() for p in (directory,*directory.parents)):
        raise ValueError('Product directory boundary is invalid')
    files={}
    for path in sorted(directory.iterdir()):
        regular(path)
        name='products/'+path.name
        if not NAME.fullmatch(name):
            raise ValueError('Unexpected file in generated product directory')
        # Inspect only front matter and hash the body; never copy bodies, URLs, or customer data.
        with path.open() as handle:
            if handle.readline().strip()!='---':raise ValueError('Missing product provenance')
            header=[]
            for _ in range(30):
                line=handle.readline()
                if line.strip()=='---':break
                header.append(line.rstrip())
            else:raise ValueError('Oversized product front matter')
        if not {'category: products','status: confirmed','source: shopify-sync'}.issubset(header):
            raise ValueError('Unreviewed product provenance')
        files[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    if not files:raise ValueError('Empty product catalog')
    raw=(json.dumps({'schema':2,'source':'shopify-sync-products','generator_sha256':digest,'directory':str(directory.resolve()),
                     'files':files},sort_keys=True,indent=2)+'\n').encode()
    with output.open('xb') as handle:
        os.chmod(output,0o600);handle.write(raw);handle.flush();os.fsync(handle.fileno())
    return {'sha256':hashlib.sha256(raw).hexdigest(),'count':len(files),'generator_sha256':digest}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--products',type=Path,required=True);parser.add_argument('--generator',type=Path,required=True)
    parser.add_argument('--expected-generator-sha256',required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(snapshot(args.products,args.generator,args.expected_generator_sha256,args.output)))
