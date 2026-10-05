import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import qa_catalog
from qa_catalog import snapshot,load_manifest

class CatalogTests(unittest.TestCase):
    def test_concurrent_replacement_cannot_pin_unvalidated_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            products = root / 'products'
            products.mkdir()
            product = products / 'product-shirt.md'
            approved = b'---\ncategory: products\nstatus: confirmed\nsource: shopify-sync\n---\nApproved shirt\n'
            product.write_bytes(approved)
            generator = root / 'sync.py'
            generator.write_text('reviewed-source')
            generator_hash = hashlib.sha256(generator.read_bytes()).hexdigest()
            validate = qa_catalog._validate_product_front_matter

            def replace_after_validation(content):
                validate(content)
                product.write_text('Unreviewed replacement without provenance\n')

            output = root / 'manifest.json'
            with patch.object(qa_catalog, '_validate_product_front_matter', side_effect=replace_after_validation):
                receipt = snapshot(products, generator, generator_hash, output)
            self.assertEqual(json.loads(output.read_text())['files']['products/product-shirt.md'],
                             hashlib.sha256(approved).hexdigest())
            with self.assertRaisesRegex(ValueError, 'Product content differs'):
                load_manifest(output, receipt['sha256'])

    def test_snapshot_pins_content_without_copying_bodies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve();products=root/'products';products.mkdir();generator=root/'sync.py';generator.write_text('reviewed-source')
            (products/'product-blue-shirt.md').write_text('---\ncategory: products\nstatus: confirmed\nsource: shopify-sync\n---\nDO-NOT-COPY-BODY https://example.invalid/private\n')
            out=root/'manifest.json';sha=hashlib.sha256(generator.read_bytes()).hexdigest()
            receipt=snapshot(products,generator,sha,out)
            self.assertNotIn('DO-NOT-COPY',out.read_text());self.assertNotIn('https',out.read_text())
            self.assertEqual(load_manifest(out,receipt['sha256']),{'products/product-blue-shirt.md'})
            (products/'product-blue-shirt.md').write_text((products/'product-blue-shirt.md').read_text().replace('BODY','EDITED'))
            with self.assertRaisesRegex(ValueError,'Product content differs'):load_manifest(out,receipt['sha256'])
            with self.assertRaises(FileExistsError):snapshot(products,generator,sha,out)
            with self.assertRaises(ValueError):load_manifest(out,'0'*64)
            (products/'product-other.md').symlink_to(products/'product-blue-shirt.md')
            with self.assertRaises(ValueError):snapshot(products,generator,sha,root/'next.json')
    def test_manifest_cannot_admit_customer_categories_or_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp).resolve()/'manifest.json'
            for name in ('tickets/customer.md','products/../customer.md','products/nested/product-x.md'):
                path.write_text(json.dumps({'schema':2,'source':'shopify-sync-products','generator_sha256':'a'*64,'directory':tmp,'files':{name:'b'*64}}))
                with self.assertRaisesRegex(ValueError,'filenames to content hashes'):load_manifest(path,hashlib.sha256(path.read_bytes()).hexdigest())
