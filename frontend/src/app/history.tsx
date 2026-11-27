// 전체 기록. 기록 탭의 최근 기록에서 [전체 보기]로 들어온다. 서버 진전도의 회차별 값(최신 회차부터).
import { StyleSheet, Text } from 'react-native';

import { Card } from '@/components/Card';
import { RecordRow } from '@/components/RecordRow';
import { Screen } from '@/components/Screen';
import { ScreenHeader } from '@/components/ScreenHeader';
import { L, useLang } from '@/i18n';
import { colors } from '@/theme';
import { sessionRows, useHistory } from '@/training/useHistory';

export default function HistoryScreen() {
  useLang();
  const { history, loading, error } = useHistory();
  const rows = sessionRows(history).map((row, i) => ({ row, index: i + 1 })).reverse();

  return (
    <Screen header={<ScreenHeader title={L('전체 기록', 'All records')} />}>
      <Card>
        <Text style={styles.count}>
          {rows.length > 0
            ? L(`총 ${rows.length}회차`, `${rows.length} sessions`)
            : loading
              ? L('불러오는 중…', 'Loading…')
              : (error ?? L('아직 기록이 없어요.', 'No records yet.'))}
        </Text>
        {rows.map(({ row, index }) => (
          <RecordRow key={row.sessionId} row={row} index={index} />
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
