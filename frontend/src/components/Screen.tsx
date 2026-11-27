import type { ReactNode } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { colors, spacing } from '@/theme';

type Props = {
  header?: ReactNode;
  footer?: ReactNode;
  scroll?: boolean;
  children: ReactNode;
};

export function Screen({ header, footer, scroll = true, children }: Props) {
  const body = scroll ? (
    <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
      {children}
    </ScrollView>
  ) : (
    <View style={[styles.content, styles.fill]}>{children}</View>
  );

  // 아래쪽도 기기 여백을 둔다. 안드로이드 아래 내비게이션 막대 밑으로 버튼이 들어가 눌리지 않는 문제(이슈 #34).
  // 여백은 이 화면이 기기 아래 막대와 겹치는 만큼만 들어가므로, 탭 막대 위에서 끝나는 탭 화면에는 0이다.
  return (
    <SafeAreaView style={styles.safe} edges={['top', 'left', 'right', 'bottom']}>
      {header}
      {body}
      {footer ? <View style={styles.footer}>{footer}</View> : null}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: {
    flex: 1,
    backgroundColor: colors.background,
  },
  content: {
    padding: spacing.lg,
    gap: spacing.lg,
  },
  fill: {
    flex: 1,
  },
  footer: {
    paddingHorizontal: spacing.lg,
    paddingBottom: spacing.xl,
  },
});
