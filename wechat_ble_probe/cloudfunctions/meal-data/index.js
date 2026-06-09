const cloud = require("wx-server-sdk");

cloud.init({ env: cloud.DYNAMIC_CURRENT_ENV });

const db = cloud.database();
const sessions = db.collection("meal_sessions");
const samples = db.collection("meal_chew_samples");

async function assertOwnedSession(sessionId, openid) {
  const result = await sessions.doc(sessionId).get();
  if (!result.data || result.data._openid !== openid) {
    throw new Error("Meal session not found or access denied");
  }
}

exports.main = async (event) => {
  const { OPENID } = cloud.getWXContext();
  const action = event.action;

  if (action === "start") {
    const result = await sessions.add({
      data: {
        _openid: OPENID,
        status: "recording",
        startTime: db.serverDate(),
        startTimeClient: event.startTimeClient,
        startTimeLocal: event.startTimeLocal,
        startTimeMs: Number(event.startTimeMs) || Date.now(),
        mode: event.mode || "intervention",
        deviceName: event.deviceName || "ChewTune",
        thresholds: event.thresholds || {},
        sampleCount: 0,
        createdAt: db.serverDate(),
        updatedAt: db.serverDate()
      }
    });
    return { sessionId: result._id };
  }

  if (action === "append") {
    const sessionId = event.sessionId;
    const points = Array.isArray(event.samples) ? event.samples.slice(0, 40) : [];
    if (!sessionId || points.length === 0) return { written: 0 };
    await assertOwnedSession(sessionId, OPENID);

    await Promise.all(points.map((point) => samples.add({
      data: {
        _openid: OPENID,
        sessionId,
        sequence: Number(point.sequence) || 0,
        recordedAt: new Date(point.recordedAtMs),
        recordedAtClient: point.recordedAtClient,
        recordedAtLocal: point.recordedAtLocal,
        recordedAtMs: Number(point.recordedAtMs) || Date.now(),
        offsetMs: Number(point.offsetMs) || 0,
        chewing: Boolean(point.chewing),
        cpm: Number(point.cpm) || 0,
        side: point.side || "-",
        stability: Number(point.stability) || 0,
        ppbSeconds: Number(point.ppbSeconds) || 0,
        ppbState: point.ppbState || "I",
        musicState: point.musicState || "pause",
        layerMask: Number(point.layerMask) || 0,
        pan: Number(point.pan) || 0,
        createdAt: db.serverDate()
      }
    })));

    await sessions.doc(sessionId).update({
      data: {
        sampleCount: db.command.inc(points.length),
        lastSampleTime: db.serverDate(),
        updatedAt: db.serverDate()
      }
    });
    return { written: points.length };
  }

  if (action === "finish") {
    if (!event.sessionId) throw new Error("sessionId is required");
    await assertOwnedSession(event.sessionId, OPENID);
    await sessions.doc(event.sessionId).update({
      data: {
        status: event.status || "completed",
        endTime: db.serverDate(),
        endTimeClient: event.endTimeClient,
        endTimeLocal: event.endTimeLocal,
        endTimeMs: Number(event.endTimeMs) || Date.now(),
        timePeriod: event.timePeriod || {},
        timePeriodLocal: event.timePeriodLocal || {},
        durationSeconds: Number(event.durationSeconds) || 0,
        pendingSampleCount: Number(event.pendingSampleCount) || 0,
        summary: event.summary || {},
        updatedAt: db.serverDate()
      }
    });
    return { sessionId: event.sessionId, finished: true };
  }

  throw new Error(`Unknown action: ${action}`);
};
