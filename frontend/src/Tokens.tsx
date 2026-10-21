import type { TokenTheme } from './appearanceConfig';

export function Crown({ className = '' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <path d="m5 9 6 6 5-10 5 10 6-6-3 16H8L5 9Z" fill="currentColor" />
      <path
        d="M11 21h10"
        stroke="var(--piece-line, #173d37)"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  );
}

function VikingFigure({ piece }: { piece: string }) {
  const king = piece === 'K';
  return (
    <svg className="viking-figure" data-token-art="viking" viewBox="0 0 64 74" aria-hidden="true">
      <ellipse cx="32" cy="67" rx="23" ry="5" fill="#16251e" opacity=".25" />
      <path
        d="M24 51 21 66h10l2-15m3 0 2 15h10l-7-17"
        fill="var(--figure-boots)"
        stroke="var(--figure-outline)"
        strokeWidth="1.5"
      />
      <path
        d="M22 33 13 61q19 8 38 0L42 33Z"
        fill="var(--figure-cloak)"
        stroke="var(--figure-outline)"
        strokeWidth="1.7"
      />
      <path
        d="m22 35 7-4h8l7 6-3 20H22Z"
        fill="var(--figure-tunic)"
        stroke="var(--figure-outline)"
        strokeWidth="1.5"
      />
      <path d="M23 46h18" stroke="var(--figure-outline)" strokeWidth="4" />
      <path d="M29 43h5v6h-5z" fill="var(--figure-metal)" />
      <path d="M12 22v43" stroke="var(--figure-outline)" strokeWidth="3" strokeLinecap="round" />
      {king ? (
        <>
          <path d="m12 10 5 7-5 6-5-6Z" fill="#e5bd63" stroke="#654b23" strokeWidth="1.5" />
          <path d="M12 24h13l-2 12H12Z" fill="var(--figure-cloak)" stroke="var(--figure-outline)" />
        </>
      ) : (
        <path
          d="m12 8 4 13h-8Z"
          fill="var(--figure-metal)"
          stroke="var(--figure-outline)"
          strokeWidth="1.4"
        />
      )}
      <circle
        cx="32"
        cy="23"
        r="10"
        fill="var(--figure-skin)"
        stroke="var(--figure-outline)"
        strokeWidth="1.6"
      />
      <path
        d="M24 26q8 16 16 0l-2 12H27Z"
        fill="var(--figure-beard)"
        stroke="var(--figure-outline)"
        strokeWidth="1.2"
      />
      {king ? (
        <path
          d="m21 18 1-11 6 5 4-8 5 8 6-5-1 12Z"
          fill="#f2ce75"
          stroke="#71501e"
          strokeWidth="1.8"
        />
      ) : (
        <>
          <path
            d="M21 22v-3a11 11 0 0 1 22 0v3Z"
            fill="var(--figure-metal)"
            stroke="var(--figure-outline)"
            strokeWidth="1.7"
          />
          <path d="M32 10v17" stroke="var(--figure-outline)" strokeWidth="2.5" />
        </>
      )}
      <path d="M27 24h2m6 0h2" stroke="#382a20" strokeWidth="1.7" strokeLinecap="round" />
      <circle
        cx="46"
        cy="47"
        r="12"
        fill="var(--figure-shield)"
        stroke="var(--figure-outline)"
        strokeWidth="2"
      />
      <path
        d="M46 36v22m-11-11h22"
        stroke="var(--figure-outline)"
        opacity=".42"
        strokeWidth="1.4"
      />
      <circle
        cx="46"
        cy="47"
        r="4"
        fill="var(--figure-metal)"
        stroke="var(--figure-outline)"
        strokeWidth="1.2"
      />
    </svg>
  );
}

function WoodCarving({ piece }: { piece: string }) {
  if (piece === 'K')
    return (
      <svg
        className="wood-carving"
        data-token-art="wood"
        data-king-design="fjord-diadem"
        viewBox="0 0 48 48"
        fill="none"
        aria-hidden="true"
      >
        <path
          d="m24 5 6 12-6 10-6-10 6-12ZM7 12l7 10-3 4H9v3H6l1-17Zm34 0 1 17h-3v-3h-2l-3-4 7-10ZM6 30q18-5 36 0v11H6V30Z"
          fill="currentColor"
        />
        <path
          d="M24 10v13M7 34l6-2 11 7 11-7 6 2M7 38l11-7 6 4 6-4 11 7"
          stroke="var(--piece-line)"
          strokeWidth="1.7"
          strokeLinejoin="miter"
        />
      </svg>
    );
  return (
    <svg
      className="wood-carving"
      data-token-art="wood"
      viewBox="0 0 40 40"
      fill="none"
      aria-hidden="true"
    >
      <path
        d={
          piece === 'A'
            ? 'M20 6v28M11 15l9-9 9 9M12 26l8-8 8 8'
            : 'M20 6v28M11 13l9 7 9-7M11 27l9-7 9 7'
        }
        stroke="currentColor"
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M7 20a13 13 0 0 1 26 0M33 20a13 13 0 0 1-26 0"
        stroke="currentColor"
        opacity=".3"
        strokeWidth="1"
      />
    </svg>
  );
}

/** Each catalog theme has a dedicated renderer; adding a theme makes missing art a type error. */
const tokenArt: Record<TokenTheme, (piece: string) => React.ReactNode> = {
  classic: (piece) => (piece === 'K' ? <Crown /> : <span className="piece-etch" />),
  viking: (piece) => <VikingFigure piece={piece} />,
  wood: (piece) => <WoodCarving piece={piece} />,
};
export function Token({ piece, theme = 'classic' }: { piece: string; theme?: TokenTheme }) {
  const side = piece === 'A' ? 'attacker' : piece === 'K' ? 'king' : 'defender';
  return (
    <span className={`piece ${side} token-${theme}`} data-token-theme={theme} aria-hidden="true">
      {tokenArt[theme](piece)}
    </span>
  );
}
