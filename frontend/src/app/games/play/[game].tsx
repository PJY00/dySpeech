// 게임 화면(라운드 하나). 게임 연결 약속(src/games/README.md)대로 GameProps를 넘기고, 라운드가 끝나면 라운드 끝 화면으로 간다.
import { useEffect, useState } from 'react';
import { router, useLocalSearchParams } from 'expo-router';
import { SafeAreaView } from 'react-native-safe-area-context';

import { PlaceholderScreen } from '@/components/PlaceholderScreen';
import { findGame } from '@/games/registry';
import { findTempGame } from '@/games/temp';
import { L, useLang } from '@/i18n';
import { colors } from '@/theme';
import { useTraining } from '@/training/TrainingProvider';

export default function GamePlayScreen() {
  useLang();
  const { game } = useLocalSearchParams<{ game: string }>();
  const entry = findGame(game ?? '') ?? findTempGame(game ?? '');
  const { baselines, userKey, levelFor, nextRoundIndex, recordRound } = useTraining();
  const [level, setLevel] = useState<number | null>(null);
  const [roundIndex] = useState(() => nextRoundIndex(game ?? ''));

  useEffect(() => {
    if (entry) void levelFor(entry.game).then(setLevel);
  }, [entry, levelFor]);

  if (!entry) {
    return <PlaceholderScreen title={L('게임', 'Game')} description={L('등록되지 않은 게임입니다.', 'This game is not registered.')} />;
  }
  if (!baselines) {
    return <PlaceholderScreen title={entry.title} description={L('측정 블록을 마친 회차가 없어요.\n홈에서 훈련을 다시 시작해 주세요.', 'No session has finished the measurement block.\nPlease start training again from Home.')} />;
  }
  if (level === null) return null;

  const Game = entry.component;
  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.background }}>
      <Game
        baselines={baselines}
        level={level}
        roundIndex={roundIndex}
        userKey={userKey}
        onRoundEnd={(run, recording) => {
          void recordRound(entry, run, recording).then(() => router.replace('/games/complete'));
        }}
        onExit={() => router.back()}
      />
    </SafeAreaView>
  );
}
