import { useState } from 'react';
import { StyleSheet, Text, View, type LayoutChangeEvent } from 'react-native';
import Svg, { Circle, Line, Polyline } from 'react-native-svg';

import { colors, spacing } from '@/theme';

export type ChartSeries = {
  label: string;
  color: string;
  values: number[];
};

type Props = {
  series: ChartSeries[];
  xLabels: string[];
  height?: number;
  showLegend?: boolean;
};

const PADDING = 12;

export function LineChart({ series, xLabels, height = 160, showLegend = true }: Props) {
  const [width, setWidth] = useState(0);

  const all = series.flatMap((s) => s.values);
  const min = Math.min(...all);
  const max = Math.max(...all);
  const span = max - min || 1;
  const count = Math.max(...series.map((s) => s.values.length));

  const x = (i: number) => PADDING + (i * (width - PADDING * 2)) / Math.max(count - 1, 1);
  const y = (v: number) => PADDING + ((max - v) * (height - PADDING * 2)) / span;

  const onLayout = (event: LayoutChangeEvent) => setWidth(event.nativeEvent.layout.width);

  return (
    <View style={styles.wrapper}>
      {showLegend ? (
        <View style={styles.legend}>
          {series.map((s) => (
            <View key={s.label} style={styles.legendItem}>
              <View style={[styles.dot, { backgroundColor: s.color }]} />
              <Text style={styles.legendText}>{s.label}</Text>
            </View>
          ))}
        </View>
      ) : null}
      <View onLayout={onLayout} style={{ height }}>
        {width > 0 ? (
          <Svg width={width} height={height}>
            {[0, 0.5, 1].map((t) => (
              <Line
                key={t}
                x1={PADDING}
                x2={width - PADDING}
                y1={PADDING + t * (height - PADDING * 2)}
                y2={PADDING + t * (height - PADDING * 2)}
                stroke={colors.border}
                strokeWidth={1}
              />
            ))}
            {series.map((s) => (
              <Polyline
                key={s.label}
                points={s.values.map((v, i) => `${x(i)},${y(v)}`).join(' ')}
                fill="none"
                stroke={s.color}
                strokeWidth={2}
              />
            ))}
            {series.map((s) =>
              s.values.map((v, i) => (
                <Circle key={`${s.label}-${i}`} cx={x(i)} cy={y(v)} r={3} fill={s.color} />
              )),
            )}
          </Svg>
        ) : null}
      </View>
      <View style={styles.xLabels}>
        {xLabels.map((label) => (
          <Text key={label} style={styles.xLabel}>
            {label}
          </Text>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrapper: {
    gap: spacing.sm,
  },
  legend: {
    flexDirection: 'row',
    justifyContent: 'flex-end',
    gap: spacing.md,
  },
  legendItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
  },
  dot: {
    width: 8,
    height: 8,
    borderRadius: 4,
  },
  legendText: {
    fontSize: 11,
    color: colors.subText,
  },
  xLabels: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingHorizontal: spacing.xs,
  },
  xLabel: {
    fontSize: 11,
    color: colors.mutedText,
  },
});
