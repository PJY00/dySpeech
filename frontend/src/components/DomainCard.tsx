import { Pressable, StyleSheet, Text, View } from 'react-native';
import { Ionicons, MaterialCommunityIcons } from '@expo/vector-icons';

import type { Domain } from '@/data/domains';
import { colors, radius, spacing } from '@/theme';

type Props = {
  domain: Domain;
  onPress: () => void;
  size?: 'compact' | 'large';
};

export function DomainCard({ domain, onPress, size = 'compact' }: Props) {
  const large = size === 'large';
  const iconBox = large ? 64 : 44;

  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [
        styles.card,
        { backgroundColor: domain.tint, borderColor: domain.tint },
        large && styles.large,
        pressed && styles.pressed,
      ]}
    >
      <View
        style={[
          styles.icon,
          { width: iconBox, height: iconBox, borderRadius: iconBox / 2, backgroundColor: domain.color },
        ]}
      >
        <MaterialCommunityIcons name={domain.icon} size={iconBox * 0.55} color="#FFFFFF" />
      </View>
      <View style={styles.text}>
        <Text style={[styles.title, large && styles.titleLarge]}>{domain.title}</Text>
        <Text style={styles.games}>{domain.games}</Text>
      </View>
      <Ionicons name="chevron-forward" size={20} color={colors.subText} />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.md,
    padding: spacing.md,
    borderRadius: radius.md,
    borderWidth: 1,
  },
  large: {
    padding: spacing.lg,
    borderRadius: radius.lg,
  },
  pressed: {
    opacity: 0.8,
  },
  icon: {
    alignItems: 'center',
    justifyContent: 'center',
  },
  text: {
    flex: 1,
    gap: 2,
  },
  title: {
    fontSize: 15,
    fontWeight: '700',
    color: colors.text,
  },
  titleLarge: {
    fontSize: 17,
  },
  games: {
    fontSize: 12,
    color: colors.subText,
  },
});
