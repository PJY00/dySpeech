import { Pressable, StyleSheet, Text } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

import { colors, radius } from '@/theme';

type Props = {
  label: string;
  onPress: () => void;
  chevron?: boolean;
};

export function PrimaryButton({ label, onPress, chevron = true }: Props) {
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [styles.button, pressed && styles.pressed]}>
      <Text style={styles.label}>{label}</Text>
      {chevron ? <Ionicons name="chevron-forward" size={18} color="#FFFFFF" style={styles.chevron} /> : null}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  button: {
    height: 54,
    borderRadius: radius.md,
    backgroundColor: colors.primary,
    alignItems: 'center',
    justifyContent: 'center',
    flexDirection: 'row',
  },
  label: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: '700',
  },
  chevron: {
    position: 'absolute',
    right: 18,
  },
  pressed: {
    opacity: 0.85,
  },
});
