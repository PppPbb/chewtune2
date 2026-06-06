const DEVICE_NAME = "ChewTune-S2"
const SERVICE_UUID = "7b100001-7c6a-4d91-a461-9c987d97b100"
const NOTIFY_UUID = "7b100002-7c6a-4d91-a461-9c987d97b100"

function bufferToAscii(buffer) {
  return String.fromCharCode.apply(null, Array.from(new Uint8Array(buffer)))
}

function sideLabel(code) {
  return {
    L: "左侧",
    R: "右侧",
    B: "双侧/未知",
    "-": "未检测"
  }[code] || code
}

function ppbLabel(code) {
  return {
    I: "未计时",
    W: "停顿倒数中",
    R: "可以开始下一口",
    T: "停顿过短"
  }[code] || code
}

Page({
  data: {
    status: "未连接",
    statusTone: "idle",
    busy: false,
    deviceId: "",
    heartbeat: "-",
    lastMessage: "-",
    messageCount: 0,
    metrics: {
      chewing: false,
      cpm: 0,
      side: "未检测",
      stability: 0,
      ppb: 0,
      ppbState: "未计时"
    },
    logs: []
  },

  onLoad() {
    wx.onBLECharacteristicValueChange((result) => {
      const message = bufferToAscii(result.value).trim()
      if (message) {
        this.handleNotify(message)
      }
    })

    wx.onBLEConnectionStateChange((result) => {
      if (!result.connected && this.data.deviceId === result.deviceId) {
        this.setData({
          status: "连接已断开",
          statusTone: "error",
          busy: false,
          deviceId: ""
        })
        this.addLog("BLE 连接已断开")
      }
    })
  },

  onUnload() {
    this.disconnect()
    wx.closeBluetoothAdapter()
  },

  startConnection() {
    if (this.data.busy) return
    this.setData({ busy: true, status: "正在打开蓝牙…", statusTone: "working" })
    this.addLog("开始连接流程")

    wx.openBluetoothAdapter({
      success: () => this.startDiscovery(),
      fail: (error) => this.failStep("无法打开蓝牙", error)
    })
  },

  startDiscovery() {
    this.setData({ status: "正在搜索 ChewTune-S2…", statusTone: "working" })
    wx.onBluetoothDeviceFound((result) => {
      const device = result.devices.find((item) => {
        return item.name === DEVICE_NAME || item.localName === DEVICE_NAME
      })
      if (device) {
        this.addLog(`发现设备 ${DEVICE_NAME}`)
        wx.stopBluetoothDevicesDiscovery()
        this.connectDevice(device.deviceId)
      }
    })

    wx.startBluetoothDevicesDiscovery({
      allowDuplicatesKey: false,
      success: () => this.addLog("蓝牙扫描已启动"),
      fail: (error) => this.failStep("启动扫描失败", error)
    })
  },

  connectDevice(deviceId) {
    this.setData({ status: "正在连接设备…", statusTone: "working" })
    wx.createBLEConnection({
      deviceId,
      timeout: 10000,
      success: () => {
        this.setData({ deviceId })
        this.addLog("设备连接成功")
        this.findService(deviceId)
      },
      fail: (error) => this.failStep("连接设备失败", error)
    })
  },

  findService(deviceId) {
    wx.getBLEDeviceServices({
      deviceId,
      success: (result) => {
        const service = result.services.find((item) => {
          return item.uuid.toLowerCase() === SERVICE_UUID
        })
        if (!service) {
          this.failStep("未找到 ChewTune 服务", { errMsg: SERVICE_UUID })
          return
        }
        this.addLog("找到 ChewTune GATT 服务")
        this.findNotifyCharacteristic(deviceId, service.uuid)
      },
      fail: (error) => this.failStep("读取服务失败", error)
    })
  },

  findNotifyCharacteristic(deviceId, serviceId) {
    wx.getBLEDeviceCharacteristics({
      deviceId,
      serviceId,
      success: (result) => {
        const characteristic = result.characteristics.find((item) => {
          return item.uuid.toLowerCase() === NOTIFY_UUID && (item.properties.notify || item.properties.indicate)
        })
        if (!characteristic) {
          this.failStep("未找到 Notify 特征", { errMsg: NOTIFY_UUID })
          return
        }
        this.enableNotify(deviceId, serviceId, characteristic.uuid)
      },
      fail: (error) => this.failStep("读取特征失败", error)
    })
  },

  enableNotify(deviceId, serviceId, characteristicId) {
    wx.notifyBLECharacteristicValueChange({
      state: true,
      deviceId,
      serviceId,
      characteristicId,
      success: () => {
        this.setData({
          status: "已连接，等待 Notify",
          statusTone: "success",
          busy: false
        })
        this.addLog("Notify 订阅成功，等待 H 和 U 消息")
      },
      fail: (error) => this.failStep("订阅 Notify 失败", error)
    })
  },

  handleNotify(message) {
    const count = this.data.messageCount + 1
    const changes = {
      lastMessage: message,
      messageCount: count,
      status: "正在接收数据",
      statusTone: "success"
    }

    if (message.startsWith("H,")) {
      changes.heartbeat = message.slice(2)
    } else if (message.startsWith("U,")) {
      const parts = message.split(",")
      if (parts.length >= 7) {
        changes.metrics = {
          chewing: parts[1] === "1",
          cpm: Number(parts[2]) || 0,
          side: sideLabel(parts[3]),
          stability: Number(parts[4]) || 0,
          ppb: (Number(parts[5]) || 0) / 10,
          ppbState: ppbLabel(parts[6])
        }
      }
    }

    this.setData(changes)
    this.addLog(message)
  },

  disconnect() {
    const deviceId = this.data.deviceId
    wx.stopBluetoothDevicesDiscovery()
    if (!deviceId) return
    wx.closeBLEConnection({ deviceId })
    this.setData({
      status: "未连接",
      statusTone: "idle",
      busy: false,
      deviceId: ""
    })
    this.addLog("已主动断开连接")
  },

  clearLogs() {
    this.setData({ logs: [], messageCount: 0, lastMessage: "-" })
  },

  addLog(message) {
    const time = new Date().toTimeString().slice(0, 8)
    const logs = [{ time, message }, ...this.data.logs].slice(0, 30)
    this.setData({ logs })
  },

  failStep(title, error) {
    const message = error && error.errMsg ? error.errMsg : String(error)
    this.setData({ status: title, statusTone: "error", busy: false })
    this.addLog(`${title}: ${message}`)
    wx.showToast({ title, icon: "none", duration: 2500 })
  }
})
