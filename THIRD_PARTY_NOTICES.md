# Third-party components

Hush integrates DeepFilterNet3 and libDF from the authors' revision
`978576aa8400552a4ce9730838c635aa30db5e61` (v0.5.6). The original code, unchanged
pretrained model and local runtime patches are described in
[vendor/PROVENANCE.md](vendor/PROVENANCE.md).

The upstream [MIT](vendor/libDF/LICENSE-MIT) and
[Apache 2.0](vendor/libDF/LICENSE-APACHE) license texts and
[license statement](vendor/libDF/LICENSE) are retained verbatim. Hush does not
claim authorship of these models or upstream code. The original README is in
[vendor/README.md](vendor/README.md).

Cargo.lock and Python package metadata identify additional dependencies. Their
respective licenses continue to apply, including when distributing binaries.
