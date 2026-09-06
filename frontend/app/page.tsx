import Link from "next/link";

import { type Showing, fetchShowings, formatShowtime } from "../lib/api";
import { BrandMark } from "./BrandMark";
import { ResetAllButton } from "./ResetAllButton";
import styles from "./page.module.css";

export const dynamic = "force-dynamic";

function artistOf(title: string): string {
  const cut = title.split(":")[0]?.trim();
  return cut || title;
}

function tourOf(title: string): string {
  const idx = title.indexOf(":");
  if (idx === -1) return "Live in a 96-seat room";
  return title.slice(idx + 1).trim();
}

function initials(title: string): string {
  const words = artistOf(title)
    .split(/\s+/)
    .filter(Boolean);
  return words
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase() ?? "")
    .join("");
}

function posterTone(title: string): string {
  const key = title.toLowerCase();
  if (key.includes("chappell")) return "rose";
  if (key.includes("swift")) return "lilac";
  if (key.includes("beyonce")) return "gold";
  if (key.includes("olivia")) return "crimson";
  if (key.includes("harry")) return "teal";
  if (key.includes("billie")) return "ink";
  if (key.includes("sabrina")) return "blush";
  if (key.includes("charli")) return "volt";
  return "rose";
}

export default async function Home() {
  let showings: Showing[] = [];
  let error: string | null = null;
  try {
    showings = await fetchShowings();
  } catch (err) {
    error = err instanceof Error ? err.message : "Failed to load showings";
  }

  const cities = [...new Set(showings.map((show) => show.venue_city))];
  const featured = showings[0];

  return (
    <div className={styles.app}>
      <header className={styles.nav}>
        <div className={styles.navInner}>
          <BrandMark className={styles.brand} />
          <nav className={styles.navLinks} aria-label="Primary">
            <a href="#tours">Tours</a>
            <a href="#how">How it works</a>
          </nav>
          <div className={styles.navTools}>
            <span className={styles.livePill}>
              <span className={styles.liveDot} aria-hidden="true" />
              Live
            </span>
            <p className={styles.navMeta}>
              {error ? "Dates unavailable" : `${showings.length} dates on sale`}
            </p>
            <ResetAllButton ids={showings.map((show) => show.id)} />
            <a className={styles.navCta} href="#tours">
              Find tickets
            </a>
          </div>
        </div>
      </header>

      <section className={styles.hero} aria-labelledby="hero-title">
        <div className={styles.heroCopy}>
          <p className={styles.eyebrow}>Official onsale</p>
          <h1 id="hero-title" className={styles.headline}>
            The room, live.
            <span>Your seat, first.</span>
          </h1>
          <p className={styles.lede}>
            Intimate pop tours in 96-seat houses. Watch the map move, hold a
            seat for 90 seconds, then make it yours.
          </p>
          <div className={styles.heroActions}>
            <a className={styles.primary} href="#tours">
              Browse tours
            </a>
            {featured ? (
              <Link className={styles.ghost} href={`/showings/${featured.id}`}>
                Open the next show
              </Link>
            ) : null}
          </div>
          <dl className={styles.proof}>
            <div>
              <dt>Hold window</dt>
              <dd>90 seconds</dd>
            </div>
            <div>
              <dt>House size</dt>
              <dd>96 seats</dd>
            </div>
            <div>
              <dt>Rule</dt>
              <dd>One fan per seat</dd>
            </div>
          </dl>
        </div>
        <div className={styles.heroArt} aria-hidden="true">
          <div className={styles.stage} />
          <div className={styles.gridPreview}>
            {Array.from({ length: 48 }, (_, i) => (
              <span
                key={i}
                className={
                  i % 7 === 0
                    ? styles.dotHeld
                    : i % 11 === 0
                      ? styles.dotSold
                      : styles.dotOpen
                }
              />
            ))}
          </div>
        </div>
      </section>

      <main className={styles.wrap}>
        <section id="tours" className={styles.listings}>
          <div className={styles.sectionHead}>
            <div>
              <p className={styles.eyebrow}>On sale now</p>
              <h2 className={styles.sectionTitle}>Upcoming tours</h2>
            </div>
            <p className={styles.count}>
              {error ? "Unavailable" : `${showings.length} dates`}
              {cities.length ? ` · ${cities.length} cities` : ""}
            </p>
          </div>

          {error ? (
            <p className={styles.error}>{error}</p>
          ) : showings.length === 0 ? (
            <p className={styles.note}>
              No dates listed yet. Seed the house from the backend, then
              refresh.
            </p>
          ) : (
            <div className={styles.cards}>
              {showings.map((showing, index) => (
                <Link
                  key={showing.id}
                  href={`/showings/${showing.id}`}
                  className={
                    index === 0 ? `${styles.card} ${styles.cardFeatured}` : styles.card
                  }
                >
                  <div
                    className={styles.poster}
                    data-tone={posterTone(showing.title)}
                  >
                    <span className={styles.posterKicker}>
                      {index === 0 ? "Featured" : showing.venue_city}
                    </span>
                    <span className={styles.posterMark}>
                      {initials(showing.title)}
                    </span>
                    <span className={styles.posterTour}>
                      {tourOf(showing.title)}
                    </span>
                  </div>
                  <div className={styles.cardBody}>
                    <h3 className={styles.showTitle}>
                      {artistOf(showing.title)}
                    </h3>
                    <p className={styles.venue}>
                      {showing.venue_name}
                      <span aria-hidden="true"> · </span>
                      {showing.venue_city}
                    </p>
                    <p className={styles.meta}>
                      <span>{formatShowtime(showing.starts_at)}</span>
                      <span>{showing.auditorium}</span>
                    </p>
                    <span className={styles.cardCta}>Find seats</span>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </section>

        <section id="how" className={styles.how}>
          <p className={styles.eyebrow}>Box office</p>
          <h2 className={styles.sectionTitle}>How a seat becomes yours</h2>
          <ol className={styles.steps}>
            <li>
              <span>01</span>
              <div>
                <h3>Open the live map</h3>
                <p>Every date is a real 8 by 12 house. Seats update as fans claim them.</p>
              </div>
            </li>
            <li>
              <span>02</span>
              <div>
                <h3>Hold for 90 seconds</h3>
                <p>A hold is yours alone. If two people click at once, only one keeps it.</p>
              </div>
            </li>
            <li>
              <span>03</span>
              <div>
                <h3>Book it</h3>
                <p>Confirm before the timer ends. Booked seats stay yours for the night.</p>
              </div>
            </li>
          </ol>
        </section>
      </main>

      <footer className={styles.footer}>
        <div className={styles.footerInner}>
          <div className={styles.footerBrand}>
            <BrandMark className={styles.brand} />
            <p>Live seat maps for rooms that still feel like a secret.</p>
          </div>
          <div>
            <p className={styles.footerLabel}>Cities</p>
            <ul>
              {(cities.length ? cities : ["Miami, FL", "Brooklyn, NY"]).map(
                (city) => (
                  <li key={city}>{city}</li>
                ),
              )}
            </ul>
          </div>
          <div>
            <p className={styles.footerLabel}>Support</p>
            <ul>
              <li>Holds expire after 90 seconds</li>
              <li>Four holds at a time</li>
              <li>No payment in this demo</li>
            </ul>
          </div>
        </div>
        <p className={styles.legal}>Velora is a demonstration box office.</p>
      </footer>
    </div>
  );
}
