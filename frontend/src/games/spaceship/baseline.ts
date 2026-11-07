import { percentile } from './audio/pitchMath';

export interface SpaceshipBaseline {
  f0MedianHz: number;
  noiseFloorDbfs: number;
  f0RangeHz?: { min: number; max: number };
}

export const VOICING_MARGIN_DB = 10;

export class LiveNoiseFloor {
  private levels: number[] = [];
  private current: number;

  constructor(initialDbfs: number) {
    this.current = initialDbfs;
  }

  addRestFrame(levelDbfs: number): void {
    this.levels.push(levelDbfs);
    if (this.levels.length > 300) this.levels.shift();
    if (this.levels.length >= 50) this.current = percentile(this.levels, 20);
  }

  get dbfs(): number {
    return this.current;
  }

  get voicingThresholdDbfs(): number {
    return this.current + VOICING_MARGIN_DB;
  }
}
