// 영역별 게임 목록. 게임 등록부(src/games/registry.ts)에 등록된 게임만 보여 준다.
import { useEffect, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { Ionicons, MaterialCommunityIcons } from '@expo/vector-icons';
import { router, useLocalSearchParams } from 'expo-router';

import { Card } from '@/components/Card';
import { PlaceholderScreen } from '@/components/PlaceholderScreen';
import { Screen } from '@/components/Screen';
import { ScreenHeader } from '@/components/ScreenHeader';
import { findDomain } from '@/data/domains';
import { gamesInArea } from '@/games/registry';
import type { GameEntry } from '@/games/registry';
import { isTempGame, tempGamesInArea } from '@/games/temp';
import { colors, radius, spacing } from '@/theme';
import { useTraining } from '@/training/TrainingProvider';

export default function DomainGamesScreen() {
  const { domain: domainId } = useLocalSearchParams<{ domain: string }>();
  const domain = findDomain(domainId);

  if (!domain) {
    return <PlaceholderScreen title="게임" description="알 수 없는 영역입니다." />;
  }

  // 등록된 실제 게임 뒤에 아직 연결되지 않은 게임의 임시 자리를 붙인다
  const games = [...gamesInArea(domain.id), ...tempGamesInArea(domain.id)];
  if (games.length === 0) {
    return <PlaceholderScreen title={`게임 ${domain.number} · ${domain.title}`} description={`${domain.games}\n아직 연결된 게임이 없어요.`} />;
  }

  return (
    <Screen header={<ScreenHeader title={`게임 ${domain.number} · ${domain.title}`} />}>
      {games.map((entry) => (
        <GameCard key={entry.game} entry={entry} color={domain.color} tint={domain.tint} icon={domain.icon} />
      ))}
    </Screen>
  );
}

function GameCard({
  entry,
  color,
  tint,
  icon,
}: {
  entry: GameEntry;
  color: string;
  tint: string;
  icon: React.ComponentProps<typeof MaterialCommunityIcons>['name'];
}) {
  const { levelFor } = useTraining();
  const [level, setLevel] = useState<number | null>(null);

  useEffect(() => {
    void levelFor(entry.game).then(setLevel);
  }, [entry.game, levelFor]);

  return (
    <Pressable onPress={() => router.push(`/games/play/${entry.game}`)} style={({ pressed }) => pressed && styles.pressed}>
      <Card style={styles.card}>
        <View style={[styles.icon, { backgroundColor: tint }]}>
          <MaterialCommunityIcons name={icon} size={28} color={color} />
        </View>
        <View style={styles.text}>
          <Text style={styles.title}>
            {entry.title}
            {isTempGame(entry) ? <Text style={styles.temp}> (임시)</Text> : null}
          </Text>
          <Text style={styles.description}>{entry.description}</Text>
          {level !== null ? (
            <Text style={styles.level}>
              단계 {level} / {entry.maxLevel}
            </Text>
          ) : null}
        </View>
        <Ionicons name="chevron-forward" size={20} color={colors.subText} />
      </Card>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  pressed: {
    opacity: 0.8,
  },
  icon: {
    width: 52,
    height: 52,
    borderRadius: radius.md,
    alignItems: 'center',
    justifyContent: 'center',
  },
  text: {
    flex: 1,
    gap: 2,
  },
  title: {
    fontSize: 16,
    fontWeight: '700',
    color: colors.text,
  },
  temp: {
    fontSize: 12,
    fontWeight: '600',
    color: colors.mutedText,
  },
  description: {
    fontSize: 12,
    color: colors.subText,
  },
  level: {
    marginTop: spacing.xs,
    fontSize: 12,
    fontWeight: '600',
    color: colors.primary,
  },
});
