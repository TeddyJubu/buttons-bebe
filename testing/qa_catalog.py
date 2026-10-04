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


def _validate_product_front_matter(path: Path) -> None:
    with path.open() as handle:
        if handle.readline().strip() != '---':
            raise ValueError('Missing product provenance')
        header = []
        for _ in range(30):
            line = handle.readline()
            if line.strip() == '---':
                break
            header.append(line.rstrip())
        else:
            raise ValueError('Oversized product front matter')
    if not {'category: products', 'status: confirmed', 'source: shopify-sync'}.issubset(header):
        raise ValueError('Unreviewed product provenance')


def _product_content_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    product_content_hashes=value['files']
    if not isinstance(product_content_hashes,dict) or not 1<=len(product_content_hashes)<=50000 or any(not NAME.fullmatch(x) or not re.fullmatch('[a-f0-9]{64}',str(h)) for x,h in product_content_hashes.items()):
        raise ValueError('Catalog manifest must map product filenames to content hashes only')
    if not re.fullmatch('[a-f0-9]{64}',value['generator_sha256']):
        raise ValueError('Missing reviewed generator digest')
    directory=Path(value['directory'])
    if not directory.is_absolute():
        raise ValueError('Catalog manifest needs the absolute product directory')
    for name, expected_content_sha256 in product_content_hashes.items():
        path=directory/PurePosixPath(name).name
        regular(path)
        if _product_content_sha256(path) != expected_content_sha256:
            raise ValueError('Product content differs from the reviewed snapshot')
    return set(product_content_hashes)


def snapshot(directory,generator,expected_generator_hash,output):
    regular(generator)
    generator_sha256=hashlib.sha256(generator.read_bytes()).hexdigest()
    if generator_sha256!=expected_generator_hash:
        raise ValueError('Product generator differs from reviewed source')
    if not directory.is_dir() or any(p.is_symlink() for p in (directory,*directory.parents)):
        raise ValueError('Product directory boundary is invalid')
    product_content_hashes: dict[str, str] = {}
    for path in sorted(directory.iterdir()):
        regular(path)
        name='products/'+path.name
        if not NAME.fullmatch(name):
            raise ValueError('Unexpected file in generated product directory')
        _validate_product_front_matter(path)
        product_content_hashes[name] = _product_content_sha256(path)
    if not product_content_hashes:raise ValueError('Empty product catalog')
    raw=(json.dumps({'schema':2,'source':'shopify-sync-products','generator_sha256':generator_sha256,'directory':str(directory.resolve()),
                     'files':product_content_hashes},sort_keys=True,indent=2)+'\n').encode()
    with output.open('xb') as handle:
        os.chmod(output,0o600);handle.write(raw);handle.flush();os.fsync(handle.fileno())
    return {'sha256':hashlib.sha256(raw).hexdigest(),'count':len(product_content_hashes),'generator_sha256':generator_sha256}


if __name__=='__main__':
    parser=argparse.ArgumentParser(
        description="Immutable manifest pinning the content of reviewed generated Shopify product docs."
    )
    parser.add_argument('--products',type=Path,required=True);parser.add_argument('--generator',type=Path,required=True)
    parser.add_argument('--expected-generator-sha256',required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(json.dumps(snapshot(args.products,args.generator,args.expected_generator_sha256,args.output)))
