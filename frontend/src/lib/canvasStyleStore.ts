import { create } from "zustand";

export const LINE_THICKNESS_MIN = 2;
export const LINE_THICKNESS_MAX = 30;
export const LINE_THICKNESS_DEFAULT = 8;
export const DOT_SIZE_MIN = 2;
export const DOT_SIZE_MAX = 20;
export const DOT_SIZE_DEFAULT = 6;

interface CanvasStyleState {
  lineThickness: number;
  dotSize: number;
  setLineThickness: (value: number) => void;
  setDotSize: (value: number) => void;
}

export const useCanvasStyleStore = create<CanvasStyleState>()((set) => ({
  lineThickness: LINE_THICKNESS_DEFAULT,
  dotSize: DOT_SIZE_DEFAULT,
  setLineThickness: (value) => set({ lineThickness: value }),
  setDotSize: (value) => set({ dotSize: value }),
}));
