// 최근 회차 카드(보내는 중 / 서버 계산 중 / 결과 나옴 / 보내지 못함). 누르면 그 회차 결과 화면.
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { router } from 'expo-router';

import { Card } from '@/components/Card';
import { formatWhen, L } from '@/i18n';
import { colors, radius, spacing } from '@/theme';
import { cardLabel, purposeLabel } from '@/training/labels';
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
      <Text style={styles.sectionTitle}>{L('최근 회차', 'Recent sessions')}</Text>
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
                {purposeLabel(s.purpose)} · {formatWhen(when)}
              </Text>
              {status === 'sending' && s.total > 0 ? (
                <Text style={styles.note}>
                  {L('녹음 올리기', 'Uploading recordings')} {s.uploaded} / {s.total}
                </Text>
              ) : null}
              {status === 'unsent' && s.lastErrorCode ? (
                <Text style={styles.note}>
                  {L('다음에 앱을 열거나 돌아올 때 다시 보내요', 'Will retry when you open or return to the app')} ({s.lastErrorCode})
                </Text>
              ) : null}
            </View>
            <Text style={[styles.badge, { color: STATUS_COLORS[status], borderColor: STATUS_COLORS[status] }]}>
              {cardLabel(status)}
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
