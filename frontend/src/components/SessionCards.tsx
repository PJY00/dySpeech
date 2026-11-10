// 최근 회차 카드(보내는 중 / 서버 계산 중 / 결과 나옴 / 보내지 못함). 누르면 그 회차 결과 화면.
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { router } from 'expo-router';

import { Card } from '@/components/Card';
import { colors, radius, spacing } from '@/theme';
import { CARD_LABELS } from '@/training/labels';
import type { SessionCard } from '@/training/labels';

const STATUS_COLORS: Record<SessionCard['status'], string> = {
  sending: '#1565C0',
  processing: '#6A1B9A',
  result: '#1B5E20',
  unsent: '#B3261E',
};

export function SessionCards({ cards }: { cards: SessionCard[] }) {
  return (
    <Card>
      <Text style={styles.sectionTitle}>최근 회차</Text>
      {cards.map(({ summary: s, status }) => {
        const when = new Date(s.startedAt);
        return (
          <Pressable
            key={s.sessionId}
            onPress={() => router.push({ pathname: '/session/result', params: { sessionId: s.sessionId } })}
            style={({ pressed }) => [styles.row, pressed && styles.pressed]}
          >
            <View style={styles.text}>
              <Text style={styles.title}>
                {s.purpose === 'retest' ? '반복 측정' : '훈련'} · {when.getMonth() + 1}월 {when.getDate()}일 {when.getHours()}시{' '}
                {when.getMinutes()}분
              </Text>
              {status === 'sending' && s.total > 0 ? (
                <Text style={styles.note}>
                  녹음 올리기 {s.uploaded} / {s.total}
                </Text>
              ) : null}
              {status === 'unsent' && s.lastErrorCode ? (
                <Text style={styles.note}>다음에 앱을 열거나 돌아올 때 다시 보내요({s.lastErrorCode})</Text>
              ) : null}
            </View>
            <Text style={[styles.badge, { color: STATUS_COLORS[status], borderColor: STATUS_COLORS[status] }]}>
              {CARD_LABELS[status]}
            </Text>
          </Pressable>
        );
      })}
    </Card>
  );
}

const styles = StyleSheet.create({
  sectionTitle: {
    fontSize: 15,
    fontWeight: '700',
    color: colors.text,
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    paddingVertical: spacing.sm,
    borderTopWidth: 1,
    borderTopColor: colors.border,
  },
  pressed: {
    opacity: 0.6,
  },
  text: {
    flex: 1,
    gap: 2,
  },
  title: {
    fontSize: 13,
    fontWeight: '600',
    color: colors.text,
  },
  note: {
    fontSize: 11,
    color: colors.subText,
  },
  badge: {
    fontSize: 11,
    fontWeight: '700',
    borderWidth: 1,
    borderRadius: radius.pill,
    paddingHorizontal: spacing.sm,
    paddingVertical: 2,
  },
});
