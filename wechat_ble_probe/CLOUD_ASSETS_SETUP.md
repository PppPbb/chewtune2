# WeChat Cloud Assets Setup

The original-quality upload package is located at:

```text
../wechat_cloud_assets/
  images/
  music/
```

## Upload assets

1. Open the mini program in WeChat DevTools.
2. Open **Cloud Development** and copy the environment ID.
3. Open **Cloud Storage**.
4. Create `images` and `music` folders.
5. Upload everything from `wechat_cloud_assets/images/` into cloud `images/`.
6. Upload everything from `wechat_cloud_assets/music/` into cloud `music/`.

Keep the filenames unchanged.

## Configure the environment

The top-level environment ID is currently configured as `cloud1-d1gkqsh7ha285800b`.
This environment belongs to mini-program AppID `wx8bef948be3989eff`; the AppID in
`project.config.json` must match or Cloud Storage audio downloads will fail on a real device.
After uploading, copy the full `cloud://...` file ID displayed by Cloud Storage into each matching
entry in `config/cloud-assets.js`.

Example:

```js
module.exports = {
  envId: "cloud1-xxxxxxxx",
  files: {
    loadingScreen: "cloud://cloud1-xxxxxxxx/images/loading-screen.png",
    background: "cloud://cloud1-xxxxxxxx/music/background.wav"
  }
};
```

If Cloud Storage displays a longer file ID, copy the full displayed `cloud://...` value instead.

The mini program loads original images and lossless music from Cloud Storage first. When cloud loading
fails, it automatically uses the lightweight files in the mini program package.
