// 측정 녹음기(react-native-audio-api 0.13.6). 녹음 설정은 여기 한곳에 둔다.
// 라이브러리 기본값이 스테레오·M4A·24비트라서 형식·채널·비트·샘플레이트를 모두 지정한다.
// 앱은 녹음을 가공하지 않는다(자르기·음량 정규화·잡음 제거 없음, api.md 5.1).

import { AudioManager, AudioRecorder, BitDepth, FileDirectory, FileFormat, FilePreset } from 'react-native-audio-api';
import type { AudioRecorderFileOptions } from 'react-native-audio-api';

import { RECORDINGS_SUBDIR } from '../session/store';
import { bufferLevelDbfs, VoiceBandFilter } from './filters';
import { classifyInputs } from './inputs';

export const RECORDING_SAMPLE_RATE = 48000;
/** 음량 콜백 버퍼: 20 ms */
const CALLBACK_FRAMES = 960;

/** 측정 녹음과 게임 녹음(kind = game)에 같이 쓰는 파일 설정: 모노·16비트 PCM·48 kHz WAV */
export const WAV_FILE_OPTIONS: AudioRecorderFileOptions = {
  format: FileFormat.Wav,
  channelCount: 1,
  preset: { ...FilePreset.High, sampleRate: RECORDING_SAMPLE_RATE, bitDepth: BitDepth.Bit16 },
  directory: FileDirectory.Document,
  subDirectory: RECORDINGS_SUBDIR,
  fileNamePrefix: 'rec',
  rotateIntervalBytes: 0,
};

export type MicPermission = 'granted' | 'denied';

/** 마이크 권한을 받고 오디오 세션을 켠다 */
export async function prepareMicrophone(): Promise<MicPermission> {
  const permission = await AudioManager.requestRecordingPermissions();
  if (permission !== 'Granted') return 'denied';
  AudioManager.setAudioSessionOptions({ iosCategory: 'playAndRecord', iosMode: 'measurement', iosOptions: [] });
  await AudioManager.setAudioSessionActivity(true);
  return 'granted';
}

export interface InputDeviceCheck {
  /** 세션의 input_device로 보내는 값 */
  inputDevice: string;
  wiredHeadset: boolean;
  /** 기기가 알려 준 입력 장치 종류(기록용) */
  categories: string[];
}

/**
 * 입력 장치 확인. 안드로이드는 currentInputs를 비워서 보내므로(라이브러리 0.13.6) availableInputs로 판단한다.
 * 유선 헤드셋이 꽂혀 있으면 기기가 그 마이크로 녹음한다고 본다
 */
export async function checkInputDevice(): Promise<InputDeviceCheck> {
  try {
    const info = await AudioManager.getDevicesInfo();
    const list = info.currentInputs.length ? info.currentInputs : info.availableInputs;
    const categories = list.map((d) => d.category);
    return { ...classifyInputs(categories), categories };
  } catch {
    return { inputDevice: 'unknown', wiredHeadset: false, categories: [] };
  }
}

export interface RecordedFile {
  fileUri: string;
  durationS: number;
}

function toFileUri(path: string): string {
  return path.startsWith('file://') ? path : `file://${path}`;
}

/** 녹음 하나씩: start(녹음 ID) → 음량 콜백 → stop() */
export class MeasurementRecorder {
  private readonly recorder = new AudioRecorder();
  private configured = false;

  /**
   * onLevel(음량 dBFS, 버퍼 길이 초, 원래 표본, 샘플레이트)은 약 20 ms마다 불린다
   * 음량은 말소리 대역(60 Hz~8 kHz) 필터를 거친 값, 표본은 필터 전 값(게임의 실시간 분석용)
   */
  async start(
    recordingId: string,
    onLevel: (levelDbfs: number, dtS: number, samples: Float32Array, sampleRate: number) => void,
  ): Promise<void> {
    let filter: VoiceBandFilter | null = null;
    if (!this.configured) {
      const r = this.recorder.enableFileOutput(WAV_FILE_OPTIONS);
      if (r.status === 'error') throw new Error(`녹음 파일 설정 실패: ${r.message}`);
      this.configured = true;
    }
    const cb = this.recorder.onAudioReady(
      { sampleRate: RECORDING_SAMPLE_RATE, bufferLength: CALLBACK_FRAMES, channelCount: 1 },
      ({ buffer }) => {
        if (!filter) filter = new VoiceBandFilter(buffer.sampleRate);
        const raw = buffer.getChannelData(0);
        const x = filter.process(raw);
        onLevel(bufferLevelDbfs(x), x.length / buffer.sampleRate, raw, buffer.sampleRate);
      },
    );
    if (cb.status === 'error') throw new Error(`음량 콜백 설정 실패: ${cb.message}`);
    const started = await this.recorder.start({ fileNameOverride: recordingId });
    if (started.status === 'error') throw new Error(`녹음 시작 실패: ${started.message}`);
  }

  async stop(): Promise<RecordedFile> {
    this.recorder.clearOnAudioReady();
    const r = await this.recorder.stop();
    if (r.status === 'error') throw new Error(`녹음 끝내기 실패: ${r.message}`);
    const path = r.paths[0];
    if (!path) throw new Error('녹음 파일 경로를 받지 못했습니다');
    return { fileUri: toFileUri(path), durationS: r.duration };
  }

  /** 화면을 벗어날 때: 녹음 중이면 멈춘다. 만든 파일은 부른 쪽이 지운다 */
  async release(): Promise<RecordedFile | null> {
    this.recorder.clearOnAudioReady();
    if (!this.recorder.isRecording() && !this.recorder.isPaused()) return null;
    try {
      return await this.stop();
    } catch {
      return null;
    }
  }
}
