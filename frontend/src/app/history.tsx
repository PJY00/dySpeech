// 전체 기록. 기록 탭의 최근 기록에서 [전체 보기]로 들어온다.
import { StyleSheet, Text } from 'react-native';

import { Card } from '@/components/Card';
import { RecordRow } from '@/components/RecordRow';
import { Screen } from '@/components/Screen';
import { ScreenHeader } from '@/components/ScreenHeader';
import { sessionRecords } from '@/data/mock';
import { colors } from '@/theme';

export default function HistoryScreen() {
  const records = [...sessionRecords].reverse();

  return (
    <Screen header={<ScreenHeader title="전체 기록" />}>
      <Card>
        <Text style={styles.count}>총 {records.length}회차</Text>
        {records.map((record) => (
          <RecordRow key={record.session} record={record} />
        ))}
      </Card>
    </Screen>
  );
}

const styles = StyleSheet.create({
  count: {
    fontSize: 13,
    color: colors.subText,
  },
});
