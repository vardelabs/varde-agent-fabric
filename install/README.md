# install

`stage-one.sh` is the one root line on a new box. It turns the box on for the box deployer, which then installs each merged change from the deployment repo.

```
curl -fsSLo stage-one.sh https://raw.githubusercontent.com/vardelabs/varde-agent-fabric/<commit>/install/stage-one.sh && echo "<sha256>  stage-one.sh" | sha256sum -c && sudo bash stage-one.sh --anchor <deployment commit>
```

Pin the URL to a commit and check the SHA-256 before you run the file, so the box runs only the reviewed copy.

It asks for one secret, the box's 1Password service account token, with no echo, and seals it with `systemd-creds`. The token must see exactly one vault, with one item named `box-bootstrap`. That item holds the box name, a tailnet join key, the git credential, the merge approvers, and the deployment repo URL. The script holds no host, account or secret.

It supports Ubuntu with systemd 250 or later. A re-run reuses the sealed token, and `--new-token` replaces it. The anchor must be on the deployment repo's `main` first-parent history.

`tests/test_stage_one.py` runs the script under a temporary root with stubbed system tools: `python3 -m unittest tests/test_stage_one.py`.
