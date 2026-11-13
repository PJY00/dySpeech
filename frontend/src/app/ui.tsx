// 앱 화면 공통 부품(React Native 기본 부품만). 글자와 단추를 크게 둔다.

import type { ReactNode } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';

export function Screen({ children }: { children: ReactNode }) {
  return (
    <ScrollView style={ui.root} contentContainerStyle={ui.content}>
      {children}
    </ScrollView>
  );
}

export function Button({
  label,
  onPress,
  kind = 'primary',
  disabled = false,
}: {
  label: string;
  onPress: () => void;
  kind?: 'primary' | 'secondary';
  disabled?: boolean;
}) {
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      style={({ pressed }) => [kind === 'primary' ? ui.primary : ui.secondary, (pressed || disabled) && ui.dim]}
    >
      <Text style={kind === 'primary' ? ui.primaryText : ui.secondaryText}>{label}</Text>
    </Pressable>
  );
}

export function Row({ label, value }: { label: string; value: string }) {
  return (
    <View style={ui.row}>
      <Text style={ui.rowLabel}>{label}</Text>
      <Text style={ui.rowValue}>{value}</Text>
    </View>
  );
}

export const ui = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#fff' },
  content: { padding: 24, paddingTop: 64, paddingBottom: 48, gap: 16 },
  title: { fontSize: 30, fontWeight: '700', color: '#111' },
  heading: { fontSize: 22, fontWeight: '700', color: '#111', marginTop: 8 },
  body: { fontSize: 20, lineHeight: 30, color: '#222' },
  note: { fontSize: 15, lineHeight: 22, color: '#555' },
  warn: { fontSize: 18, lineHeight: 26, color: '#b3261e' },
  ok: { fontSize: 20, color: '#1b5e20', fontWeight: '600' },
  mono: { fontSize: 12, fontFamily: 'monospace', color: '#444' },
  card: { padding: 16, borderRadius: 14, backgroundColor: '#f3f6fb', gap: 10 },
  primary: { backgroundColor: '#1976d2', borderRadius: 14, paddingVertical: 18, alignItems: 'center' },
  secondary: { borderColor: '#1976d2', borderWidth: 2, borderRadius: 14, paddingVertical: 16, alignItems: 'center' },
  dim: { opacity: 0.6 },
  primaryText: { color: '#fff', fontSize: 20, fontWeight: '700' },
  secondaryText: { color: '#1976d2', fontSize: 18, fontWeight: '600' },
  row: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: '#e6e6e6', gap: 12 },
  rowLabel: { fontSize: 17, color: '#333', flexShrink: 1 },
  rowValue: { fontSize: 17, fontWeight: '700', color: '#111' },
});
