# Copyright 2018 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Classes for reading and writing SAM and BAM files.

The SAM/BAM format is described at
https://samtools.github.io/hts-specs/SAMv1.pdf

API for reading:

```python
from nucleus.io import sam

with sam.SamReader(input_path) as reader:
  for read in reader:
    print(read)
```

where `read` is a `nucleus.genomics.v1.Read` protocol buffer.

API for writing:

```python
from nucleus.io import sam

# reads is an iterable of nucleus.genomics.v1.Read protocol buffers.
reads = ...

with sam.SamWriter(output_path) as writer:
  for read in reads:
    writer.write(read)
```

For both reading and writing, if the path provided to the constructor contains
'.tfrecord' as an extension, a `TFRecord` file is assumed and attempted to be
read or written. Otherwise, the filename is treated as a true SAM/BAM file.

For `TFRecord` files, ending in a '.gz' suffix causes the file to be treated as
compressed with gzip.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from nucleus.io import genomics_reader
from nucleus.io import genomics_writer
from nucleus.io.python import sam_reader
from nucleus.protos import reads_pb2
from nucleus.util import ranges
from nucleus.util import utils


class NativeSamReader(genomics_reader.GenomicsReader):
  """Class for reading from native SAM/BAM files.

  Most users will want to use SamReader instead, because it dynamically
  dispatches between reading native SAM/BAM files and TFRecord files based
  on the filename's extensions.
  """

  def __init__(self, input_path,
               read_requirements=None,
               parse_aux_fields=False,
               hts_block_size=None,
               downsample_fraction=None,
               random_seed=None):
    """Initializes a NativeSamReader.

    Args:
      input_path: str. A path to a resource containing SAM/BAM records.
        Currently supports SAM text format and BAM binary format.
      read_requirements: optional ReadRequirement proto. If not None, this proto
        is used to control which reads are filtered out by the reader before
        they are passed to the client.
      parse_aux_fields: optional bool, defaulting to False. If False, we do not
        parse the auxiliary fields of the SAM/BAM records (see SAM spec for
        details). Parsing the aux fields is unnecessary for many applications,
        and adds a significant parsing cost to access. If you need these aux
        fields, set parse_aux_fields to True and these fields will be parsed and
        populate the appropriate Read proto fields (e.g., read.info).
      hts_block_size: int or None. If specified, this configures the block size
        of the underlying htslib file object. Larger values (e.g. 1M) may be
        beneficial for reading remote files. If None, the reader uses the
        default htslib block size.
      downsample_fraction: float in the interval [0.0, 1.0] or None. If
        specified as a positive float, the reader will only keep each read with
        probability downsample_fraction, randomly. If None or zero, all reads
        are kept.
      random_seed: None or int. The random seed to use with this sam reader, if
        needed. If None, a fixed random value will be assigned.

    Raises:
      ValueError: If downsample_fraction is not None and not in the interval
        (0.0, 1.0].
      ImportError: If someone tries to load a tfbam file.
    """
    if input_path.endswith('.tfbam'):
      # Delayed loading of tfbam_lib.
      try:
        from tfbam_lib import tfbam_reader  # pylint: disable=g-import-not-at-top
        self._reader = tfbam_reader.make_sam_reader(
            input_path,
            read_requirements=read_requirements,
            unused_block_size=hts_block_size,
            downsample_fraction=downsample_fraction,
            random_seed=random_seed)
      except ImportError:
        raise ImportError(
            'tfbam_lib module not found, cannot read .tfbam files.')
    else:
      aux_field_handling = reads_pb2.SamReaderOptions.SKIP_AUX_FIELDS
      if parse_aux_fields:
        aux_field_handling = reads_pb2.SamReaderOptions.PARSE_ALL_AUX_FIELDS

      # We make 0 be a valid value that means "keep all reads" so that proto
      # defaults (=0) do not omit all reads.
      if downsample_fraction is not None and downsample_fraction != 0:
        if not 0.0 < downsample_fraction <= 1.0:
          raise ValueError(
              'downsample_fraction must be in the interval (0.0, 1.0]',
              downsample_fraction)

      if random_seed is None:
        # Fixed random seed produced with 'od -vAn -N4 -tu4 < /dev/urandom'.
        random_seed = 2928130004

      self._reader = sam_reader.SamReader.from_file(
          input_path.encode('utf8'),
          reads_pb2.SamReaderOptions(
              read_requirements=read_requirements,
              aux_field_handling=aux_field_handling,
              hts_block_size=(hts_block_size or 0),
              downsample_fraction=downsample_fraction,
              random_seed=random_seed))

      self.header = self._reader.header

    super(NativeSamReader, self).__init__()

  def iterate(self):
    """Returns an iterable of Read protos in the file."""
    return self._reader.iterate()

  def query(self, region):
    """Returns an iterator for going through the reads in the region."""
    return self._reader.query(region)

  def __exit__(self, exit_type, exit_value, exit_traceback):
    self._reader.__exit__(exit_type, exit_value, exit_traceback)


class SamReader(genomics_reader.DispatchingGenomicsReader):
  """Class for reading Read protos from SAM or TFRecord files."""

  def _native_reader(self, input_path, **kwargs):
    return NativeSamReader(input_path, **kwargs)

  def _record_proto(self):
    return reads_pb2.Read


class NativeSamWriter(genomics_writer.GenomicsWriter):
  """Class for writing to native SAM/BAM files.

  Most users will want SamWriter, which will write to either native SAM/BAM
  files or TFRecords files, based on the output filename's extensions.
  """

  def __init__(self, output_path, header):
    """Initializer for NativeSamWriter.

    Args:
      output_path: str. A path where we'll write our SAM/BAM file.
      header: A nucleus.SamHeader proto.  The header is used both for writing
        the header, and to control the sorting applied to the rest of the file.
    """
    raise NotImplementedError

  def write(self, proto):
    raise NotImplementedError

  def __exit__(self, exit_type, exit_value, exit_traceback):
    self._writer.__exit__(exit_type, exit_value, exit_traceback)


class SamWriter(genomics_writer.DispatchingGenomicsWriter):
  """Class for writing Variant protos to SAM or TFRecord files."""

  def _native_writer(self, output_path, header):
    return NativeSamWriter(output_path, header)


class InMemorySamReader(object):
  """Python interface class for in-memory SAM/BAM reader.

  Attributes:
    reads: list[nucleus.genomics.v1.Read]. The list of in-memory reads.
    is_sorted: bool, True if reads are sorted.
  """

  def __init__(self, reads, is_sorted=False):
    self.replace_reads(reads, is_sorted=is_sorted)

  def replace_reads(self, reads, is_sorted=False):
    """Replace the reads stored by this reader."""
    self.reads = reads
    self.is_sorted = is_sorted

  def iterate(self):
    """Iterate over all records in the reads.

    Returns:
      An iterator over nucleus.genomics.v1.Read's.
    """
    return self.reads

  def query(self, region):
    """Returns an iterator for going through the reads in the region.

    Args:
      region: nucleus.genomics.v1.Range. The query region.

    Returns:
      An iterator over nucleus.genomics.v1.Read protos.
    """
    # TODO(b/37353140): Add a faster query version for sorted reads.
    return (read for read in self.reads
            if ranges.ranges_overlap(region, utils.read_range(read)))
