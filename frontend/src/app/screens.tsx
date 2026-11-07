// 앱 화면들(표시만). 흐름과 세션 처리는 AppShell.tsx가 한다.

import { ActivityIndicator, Text, View } from 'react-native';

import { GAME_AREAS, gamesInArea } from '../games/registry';
import type { GameEntry } from '../games/registry';
import type { GameRun, Session, SessionProgress } from '../session';
import { unfinishedOptions } from './flow';
import { formatMeasurement, METRIC_LABELS, STATE_LABELS } from './labels';
import { Button, Row, Screen, ui } from './ui';

export function HomeScreen(props: {
  notice: string | null;
  waiting: number;
  onTraining: () => void;
  onRetest: () => void;
  onRecords: () => void;
}) {
  return (
    <Screen>
      <Text style={ui.title}>오늘의 발성 훈련</Text>
      {props.notice && <Text style={ui.warn}>{props.notice}</Text>}
      <Text style={ui.body}>목소리를 재고, 게임으로 연습해요. 조용한 곳에서 유선 헤드셋을 써 주세요.</Text>
      <Button label="오늘 훈련 시작" onPress={props.onTraining} />
      <Button label="반복 측정" kind="secondary" onPress={props.onRetest} />
      <Button label="기록" kind="secondary" onPress={props.onRecords} />
      {props.waiting > 0 && <Text style={ui.note}>아직 서버로 보내지 못한 회차가 {props.waiting}개 있어요. 인터넷에 연결되면 보내요.</Text>}
    </Screen>
  );
}

export function ResumeScreen(props: { sessions: Session[]; onSend: (s: Session) => void; onDiscard: (s: Session) => void; onDone: () => void }) {
  return (
    <Screen>
      <Text style={ui.title}>끝나지 않은 회차가 있어요</Text>
      <Text style={ui.body}>앱이 중간에 꺼졌던 것 같아요. 회차마다 보낼지, 버릴지 골라 주세요.</Text>
      {props.sessions.map((s) => (
        <ResumeCard key={s.id} session={s} onSend={() => props.onSend(s)} onDiscard={() => props.onDiscard(s)} />
      ))}
      {props.sessions.length === 0 && <Button label="처음으로" onPress={props.onDone} />}
    </Screen>
  );
}

function ResumeCard({ session, onSend, onDiscard }: { session: Session; onSend: () => void; onDiscard: () => void }) {
  const p = session.status();
  const when = new Date(session.startedAt);
  const options = unfinishedOptions(session);
  return (
    <View style={ui.card}>
      <Text style={ui.body}>
        {session.purpose === 'retest' ? '반복 측정' : '훈련'} · {when.getMonth() + 1}월 {when.getDate()}일 {when.getHours()}시 {when.getMinutes()}분 · 녹음 {p.total}개
      </Text>
      {options.reason && <Text style={ui.note}>{options.reason}</Text>}
      {options.canSend && <Button label="지금까지 한 것 보내기" onPress={onSend} />}
      <Button label="버리기" kind={options.canSend ? 'secondary' : 'primary'} onPress={onDiscard} />
    </View>
  );
}

export function ChooseScreen(props: {
  dev: boolean;
  roundsDone: number;
  onPick: (game: GameEntry) => void;
  onFinish: () => void;
}) {
  return (
    <Screen>
      <Text style={ui.title}>게임 고르기</Text>
      {props.dev && <Text style={ui.warn}>개발용 바로가기: 임시 기준값을 쓰고, 서버로 보내지 않아요.</Text>}
      {GAME_AREAS.map((a) => {
        const games = gamesInArea(a.area);
        return (
          <View key={a.area} style={ui.card}>
            <Text style={ui.heading}>{a.title}</Text>
            <Text style={ui.note}>{a.description}</Text>
            {games.map((g) => (
              <Button key={g.game} label={g.title} onPress={() => props.onPick(g)} />
            ))}
            {games.length === 0 && <Text style={ui.note}>준비 중이에요.</Text>}
          </View>
        );
      })}
      <Text style={ui.note}>오늘 한 라운드: {props.roundsDone}개</Text>
      <Button label="오늘 훈련 끝" kind="secondary" onPress={props.onFinish} />
    </Screen>
  );
}

const DECISION_TEXT: Record<GameRun['decision'], string> = {
  up: '다음 라운드는 한 단계 올라가요',
  down: '다음 라운드는 한 단계 쉬워져요',
  hold: '다음 라운드도 같은 단계예요',
  deferred: '이번 라운드는 판정을 미뤘어요',
};

export function RoundEndScreen(props: { game: GameEntry | undefined; run: GameRun; nextLevel: number; onNext: () => void; onOther: () => void; onFinish: () => void }) {
  return (
    <Screen>
      <Text style={ui.title}>라운드 끝</Text>
      <Row label="게임" value={props.game?.title ?? props.run.game} />
      <Row label="성공률" value={`${Math.round(props.run.success_rate_pct)}%`} />
      <Row label="이번 단계" value={String(props.run.level)} />
      <Text style={ui.body}>{DECISION_TEXT[props.run.decision]}</Text>
      {props.run.decision_reason ? <Text style={ui.note}>{props.run.decision_reason}</Text> : null}
      <Button label={`다음 라운드(단계 ${props.nextLevel})`} onPress={props.onNext} />
      <Button label="다른 게임 고르기" kind="secondary" onPress={props.onOther} />
      <Button label="오늘 훈련 끝" kind="secondary" onPress={props.onFinish} />
    </Screen>
  );
}

export function FinishingScreen({ progress }: { progress: SessionProgress | null }) {
  return (
    <Screen>
      <Text style={ui.title}>결과를 기다리고 있어요</Text>
      <ActivityIndicator size="large" />
      <Text style={ui.body}>{progress ? STATE_LABELS[progress.state] : '저장하는 중'}</Text>
      {progress && progress.total > 0 && (
        <Text style={ui.note}>
          녹음 올리기 {progress.uploaded} / {progress.total}
        </Text>
      )}
    </Screen>
  );
}

export function ResultScreen(props: {
  progress: SessionProgress | null;
  devRuns: GameRun[] | null;
  checking: boolean;
  onCheck: () => void;
  onHome: () => void;
}) {
  if (props.devRuns) {
    return (
      <Screen>
        <Text style={ui.title}>개발용 결과(보내지 않음)</Text>
        <Text style={ui.note}>라운드 {props.devRuns.length}개. 기록은 Metro 로그에도 남겼어요.</Text>
        {props.devRuns.map((r) => (
          <Text key={r.game_run_id} style={ui.mono} selectable>
            {JSON.stringify(r)}
          </Text>
        ))}
        <Button label="처음으로" onPress={props.onHome} />
      </Screen>
    );
  }
  const p = props.progress;
  const server = p?.server;
  const headline =
    p?.state === 'done'
      ? '서버 계산이 끝났어요'
      : p?.state === 'failed'
        ? '서버 계산에 실패했어요'
        : p?.state === 'processing'
          ? '서버가 아직 계산하고 있어요'
          : p?.state === 'blocked'
            ? '보내기 중 오류가 났어요'
            : '아직 보내지 못했어요';
  return (
    <Screen>
      <Text style={ui.title}>{headline}</Text>
      {p?.state === 'queued' && <Text style={ui.body}>인터넷 연결을 확인해 주세요. 앱을 다시 열면 이어서 보내요.</Text>}
      {p?.state === 'processing' && <Text style={ui.body}>잠시 뒤에 다시 확인해 주세요.</Text>}
      {server?.error && <Text style={ui.warn}>오류 코드: {server.error.code}</Text>}
      {p?.lastError && p.state !== 'done' && (
        <Text style={ui.warn}>
          보내기 오류: {p.lastError.code}
          {p.lastError.message ? ` ${p.lastError.message}` : ''}
        </Text>
      )}
      {server?.server_measurements.map((m) => (
        <Row key={`${m.metric}-${m.algorithm_version}`} label={METRIC_LABELS[m.metric] ?? m.metric} value={formatMeasurement(m)} />
      ))}
      {server && server.server_measurements.length > 0 && <Text style={ui.note}>서버가 녹음에서 계산한 값이에요. 음량은 상대값(dBFS)이에요.</Text>}
      {p?.state !== 'done' && p?.state !== 'failed' && (
        <Button label={props.checking ? '확인하는 중…' : '다시 확인'} onPress={props.onCheck} disabled={props.checking} />
      )}
      <Button label="처음으로" kind="secondary" onPress={props.onHome} />
    </Screen>
  );
}

export function RecordsScreen({ onHome }: { onHome: () => void }) {
  return (
    <Screen>
      <Text style={ui.title}>기록</Text>
      <Text style={ui.body}>진전도 화면은 준비 중이에요.</Text>
      <Button label="처음으로" onPress={onHome} />
    </Screen>
  );
}
