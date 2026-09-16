"""Re-encode every array in a store with gzip (one-off, after the zstd chr4 build)."""
import sys, zarr, numpy as np
from zarr.codecs import GzipCodec
root = zarr.open_group(sys.argv[1], mode='r+')
for name, arr in root['hg38'].arrays():
    if any(type(c).__name__ == 'GzipCodec' for c in arr.compressors):
        print(name, 'already gzip'); continue
    attrs = dict(arr.attrs)
    tmp = root['hg38'].create_array(f'{name}__gz', shape=arr.shape, chunks=arr.chunks, dtype=arr.dtype,
                                    fill_value=arr.fill_value, compressors=[GzipCodec(level=6)],
                                    config={'write_empty_chunks': False})
    tmp.attrs.update(attrs)
    off = root['hg38'].attrs['chrom_offsets']; sizes = root['hg38'].attrs['chrom_sizes']
    for c in attrs.get('chroms_done', []):
        a, b = off[c], off[c] + sizes[c] // root['hg38'].attrs['bin_bp']
        tmp[:, a:b] = arr[:, a:b]; print(name, c, 'copied')
    import shutil, pathlib
    p = pathlib.Path(sys.argv[1]) / 'hg38'
    shutil.rmtree(p / name); (p / f'{name}__gz').rename(p / name)
    print(name, '-> gzip')
