// 2. 게임 선택 화면
import { StyleSheet, Text, View } from 'react-native';
import { MaterialCommunityIcons } from '@expo/vector-icons';
import { router } from 'expo-router';

import { DomainCard } from '@/components/DomainCard';
import { Screen } from '@/components/Screen';
import { ScreenHeader } from '@/components/ScreenHeader';
import { domains } from '@/data/domains';
import { colors, spacing } from '@/theme';

export default function GameSelectScreen() {
  return (
    <Screen header={<ScreenHeader title="게임 선택" />}>
      {domains.map((domain) => (
        <DomainCard
          key={domain.id}
          domain={domain}
          size="large"
          onPress={() => router.push(`/games/${domain.id}`)}
        />
      ))}
      <View style={styles.hint}>
        <Text style={styles.hintText}>게임을 선택하면{'\n'}훈련이 시작됩니다.</Text>
        <MaterialCommunityIcons name="image-filter-hdr" size={56} color={colors.border} />
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  hint: {
    alignItems: 'center',
    gap: spacing.lg,
    marginTop: spacing.xl,
  },
  hintText: {
    fontSize: 14,
    color: colors.subText,
    textAlign: 'center',
    lineHeight: 20,
  },
});
