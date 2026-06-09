# ChewTune 云数据库部署

## 数据结构

### `meal_sessions`

每一餐一条记录：

- `startTime` / `endTime`：云端服务器时间
- `startTimeClient` / `endTimeClient`：手机端 ISO 时间
- `startTimeLocal` / `endTimeLocal`：手机本地可读时间
- `timePeriod.start` / `timePeriod.end`：本餐记录时间段
- `timePeriodLocal.start` / `timePeriodLocal.end`：本餐本地可读时间段
- `durationSeconds`：持续秒数
- `mode`：`assessment` 或 `intervention`
- `thresholds`：本餐使用的 CPM 与 PPB 阈值
- `sampleCount`：已保存的实时数据数量
- `summary`：本餐汇总数据

### `meal_chew_samples`

每个实时咀嚼数据点一条记录：

- `sessionId`：所属餐次
- `sequence`：餐内顺序编号
- `recordedAt` / `recordedAtClient`：记录时间
- `recordedAtLocal`：手机本地可读记录时间
- `offsetMs`：距本餐开始的毫秒数
- `chewing`、`cpm`、`side`、`stability`
- `ppbSeconds`、`ppbState`
- `musicState`、`layerMask`、`pan`

## 部署步骤

1. 在微信开发者工具打开“云开发”，确认环境为 `cloud1-d1gkqsh7ha285800b`。
2. 在云数据库创建集合：
   - `meal_sessions`
   - `meal_chew_samples`
3. 在 `meal_chew_samples` 创建组合索引：
   - `sessionId` 升序
   - `sequence` 升序
4. 在开发者工具中右键 `cloudfunctions/meal-data`，选择“上传并部署：云端安装依赖”。
5. 重新编译并进行真机调试。

云函数使用用户 OpenID 隔离数据。查询某一餐的完整时间序列时，对
`meal_chew_samples` 使用对应 `sessionId`，并按 `sequence` 升序排列。
