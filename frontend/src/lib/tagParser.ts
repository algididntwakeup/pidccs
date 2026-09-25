/**
 * Parser for P&ID Piping Line Tags / IDs.
 * Supports PetroChina standard, Pertamina standard, and general heuristic formats.
 */

export interface ParsedPipingTag {
  unit: string;
  size: string;
  fluid: string;
  pclass: string;
  seq: string;
}

export function parsePipingIdTag(raw: string): ParsedPipingTag {
  const s = (raw || '').trim();
  if (!s) {
    return { unit: '', size: '', fluid: '', pclass: '', seq: '' };
  }

  // Normalize quotes: ” ″ ’ ‘ ' -> "
  const norm = s
    .replace(/[”″’‘'`]/g, '"')
    .replace(/\s+/g, '');

  // 1. PetroChina schema: 695-6"-GF-CCB-026 or 605-12"-GR-CDA-012[-suffix]
  // Format: unit - size - fluid - class - seq [- suffix...]
  const petroRegex = /^(\d{2,4})-?(\d{1,2}(?:[-/]\d\/\d|\/\d)?["']?)-([A-Za-z]{1,4})-([A-Za-z0-9]{2,4})-(\d{1,4}[A-Za-z]{0,3})(?:-(.+))?$/;
  const petroMatch = norm.match(petroRegex);
  if (petroMatch) {
    return {
      unit: petroMatch[1] || '',
      size: petroMatch[2] || '',
      fluid: petroMatch[3]?.toUpperCase() || '',
      pclass: petroMatch[4]?.toUpperCase() || '',
      seq: petroMatch[5] || '',
    };
  }

  // 2. Pertamina schema: 14-P2-1802-A1A2-6"
  // Format: unit - fluid - seq - class - size [- suffix...]
  const pertaRegex = /^(\d{1,4})-([A-Za-z]{1,3}\d?)-(\d{3,5})-([A-Za-z][A-Za-z0-9]{2,4})-(\d{1,2}(?:[-/]\d\/\d)?["']?)(?:-(.+))?$/;
  const pertaMatch = norm.match(pertaRegex);
  if (pertaMatch) {
    return {
      unit: pertaMatch[1] || '',
      fluid: pertaMatch[2]?.toUpperCase() || '',
      seq: pertaMatch[3] || '',
      pclass: pertaMatch[4]?.toUpperCase() || '',
      size: pertaMatch[5] || '',
    };
  }

  // 3. Fallback heuristic: split by '-'
  const parts = norm.split('-').filter(Boolean);
  let unit = '', size = '', fluid = '', pclass = '', seq = '';

  for (let i = 0; i < parts.length; i++) {
    const part = parts[i];
    if (part.includes('"') || (/^\d+(\/\d+)?$/.test(part) && !size && i > 0)) {
      if (!size) size = part;
    } else if (/^[A-Za-z]{1,4}$/.test(part) && !fluid) {
      fluid = part.toUpperCase();
    } else if (/^[A-Za-z][A-Za-z0-9]{2,4}$/.test(part) && !pclass) {
      pclass = part.toUpperCase();
    } else if (/^\d{3,5}[A-Za-z]?$/.test(part) && !seq) {
      seq = part;
    } else if (/^\d{2,4}$/.test(part) && !unit && i === 0) {
      unit = part;
    }
  }

  return { unit, size, fluid, pclass, seq };
}
