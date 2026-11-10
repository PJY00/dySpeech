// 아직 내용이 없는 화면. 제목, 안내 문구, 다음 버튼만 둔다.
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { PrimaryButton } from '@/components/PrimaryButton';
import { Screen } from '@/components/Screen';
import { ScreenHeader } from '@/components/ScreenHeader';
import { colors, spacing } from '@/theme';

type Props = {
  title: string;
  description: string;
  nextLabel?: string;
  onNext?: () => void;
  secondaryLabel?: string;
  onSecondary?: () => void;
  back?: boolean;
};

export function PlaceholderScreen({
  title,
  description,
  nextLabel = '다음',
  onNext,
  secondaryLabel,
  onSecondary,
  back = true,
}: Props) {
  return (
    <Screen
      scroll={false}
      header={<ScreenHeader title={title} back={back} />}
      footer={
        onNext ? (
          <View style={styles.actions}>
            <PrimaryButton label={nextLabel} onPress={onNext} />
            {secondaryLabel && onSecondary ? (
              <Pressable onPress={onSecondary} hitSlop={8}>
                <Text style={styles.secondary}>{secondaryLabel}</Text>
              </Pressable>
            ) : null}
          </View>
        ) : undefined
      }
    >
      <View style={styles.center}>
        <Text style={styles.title}>{title}</Text>
        <Text style={styles.description}>{description}</Text>
        <Text style={styles.badge}>준비 중인 화면입니다</Text>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  actions: {
    gap: spacing.md,
  },
  secondary: {
    textAlign: 'center',
    fontSize: 14,
    fontWeight: '600',
    color: colors.subText,
  },
  center: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.sm,
  },
  title: {
    fontSize: 22,
    fontWeight: '800',
    color: colors.text,
  },
  description: {
    fontSize: 14,
    color: colors.subText,
    textAlign: 'center',
  },
  badge: {
    marginTop: spacing.md,
    fontSize: 12,
    color: colors.mutedText,
  },
});
