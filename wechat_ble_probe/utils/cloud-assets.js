const config = require("../config/cloud-assets");

const cache = {};

async function resolve(names, fallbacks = {}) {
  const result = { ...fallbacks };
  const missing = names.filter((name) => !cache[name]);
  names.forEach((name) => {
    if (cache[name]) result[name] = cache[name];
  });

  if (!config.envId || !wx.cloud || missing.length === 0) return result;
  try {
    const response = await wx.cloud.getTempFileURL({
      fileList: missing.map((name) => config.files[name])
    });
    response.fileList.forEach((file, index) => {
      if (file.tempFileURL) {
        cache[missing[index]] = file.tempFileURL;
        result[missing[index]] = file.tempFileURL;
      }
    });
  } catch (error) {
    console.warn("Cloud asset fallback:", error);
  }
  return result;
}

async function download(names, fallbacks = {}, onStatus = () => {}) {
  const result = { ...fallbacks };
  if (!config.envId || !wx.cloud) {
    onStatus("cloud storage unavailable");
    return result;
  }

  const urls = await requestTempURLs(names, onStatus);
  if (!urls) return result;

  await Promise.all(names.map(async (name) => {
    if (!urls[name]) {
      onStatus(`${name}: empty cloud URL`);
      return;
    }
    try {
      onStatus(`${name}: downloading`);
      const response = await new Promise((resolve, reject) => {
        wx.downloadFile({
          url: urls[name],
          success: resolve,
          fail: reject
        });
      });
      if (response.statusCode && response.statusCode !== 200) {
        onStatus(`${name}: download HTTP ${response.statusCode}`);
        onStatus(`${name}: using local fallback`);
      } else if (response.tempFilePath) {
        result[name] = response.tempFilePath;
        onStatus(`${name}: downloaded`);
      } else {
        onStatus(`${name}: using local fallback`);
      }
    } catch (error) {
      onStatus(`${name}: download failed ${error.errMsg || error}`);
      onStatus(`${name}: using local fallback`);
    }
  }));
  return result;
}

async function requestTempURLs(names, onStatus) {
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      onStatus(`requesting cloud URLs (${attempt}/3)`);
      const response = await wx.cloud.getTempFileURL({
        fileList: names.map((name) => config.files[name])
      });
      const urls = {};
      response.fileList.forEach((file, index) => {
        const name = names[index];
        if (file.tempFileURL) {
          urls[name] = file.tempFileURL;
          onStatus(`${name}: cloud URL ready`);
        } else {
          const detail = file.errMsg || file.status || "empty URL";
          onStatus(`${name}: cloud URL unavailable ${detail}`);
        }
      });
      if (Object.keys(urls).length > 0) return urls;
    } catch (error) {
      onStatus(`cloud URL request failed ${error.errCode || ""} ${error.errMsg || error}`);
    }
    await new Promise((resolve) => setTimeout(resolve, 700 * attempt));
  }
  return null;
}

module.exports = { config, resolve, download };
