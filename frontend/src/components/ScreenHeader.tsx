import type { ReactNode } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { router } from 'expo-router';

import { colors, spacing } from '@/theme';

type Props = {
  title: string;
  back?: boolean;
  right?: ReactNode;
};

export function ScreenHeader({ title, back = true, right }: Props) {
  return (
    <View style={styles.header}>
      <View style={styles.side}>
        {back ? (
          <Pressable onPress={() => router.back()} hitSlop={12} accessibilityLabel="뒤로 가기">
            <Ionicons name="chevron-back" size={24} color={colors.text} />
          </Pressable>
        ) : null}
      </View>
      <Text style={styles.title} numberOfLines={1}>
        {title}
      </Text>
      <View style={[styles.side, styles.right]}>{right}</View>
    </View>
  );
}

const styles = StyleSheet.create({
  header: {
    height: 52,
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: spacing.lg,
  },
  side: {
    width: 88,
  },
  right: {
    alignItems: 'flex-end',
  },
  title: {
    flex: 1,
    textAlign: 'center',
    fontSize: 17,
    fontWeight: '700',
    color: colors.text,
  },
});
