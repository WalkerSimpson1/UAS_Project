-- Short policy names for narrow Datawrapper columns (docs/DECISIONS.md P30).
-- policies.short_title is a hand-set name; when it's empty, short_name(title) trims the title by rule.
-- Export as coalesce(p.short_title, short_name(p.title)).
BEGIN;

ALTER TABLE policies ADD COLUMN short_title text CHECK (length(short_title) <= 80);

CREATE FUNCTION short_name(title text) RETURNS text LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    t text := btrim(title);
    comment_req boolean := t ~* '^(notice of request for public comments on|request for (public )?comments? on|seeking comment on|comment sought on)\s';
BEGIN
    t := regexp_replace(t, '^(notice of request for public comments on|request for (public )?comments? on|seeking comment on|comment sought on)\s+', '', 'i');
    t := regexp_replace(t, '^a bill to (require |provide for |establish )?(an? |the )?', '', 'i');
    t := regexp_replace(t, '^implementing certain tariff-related elements of (the )?', 'Tariff elements of ', 'i');
    t := regexp_replace(t, '^([A-Z]{3,}) Act\y.*$', '\1 Act');                  -- "LEAD Act Leading Exports ..." -> "LEAD Act"
    t := regexp_replace(t, 'unmanned aircraft systems?( \(UAS\))?', 'UAS', 'gi');
    t := regexp_replace(t, 'national defense authorization act for fiscal year (\d{4})', 'NDAA FY\1', 'i');
    t := regexp_replace(t, '\.$', '');
    t := upper(left(t, 1)) || substr(t, 2);
    IF comment_req THEN t := t || ' (comment request)'; END IF;
    IF length(t) > 70 THEN                                                        -- cut at a word, add an ellipsis
        t := regexp_replace(left(t, 69), '[\s,;:(/-]+\S*$', '') || '…';
    END IF;
    RETURN t;
END $$;

-- Hand-set names for today's long titles (rules can't summarize these well).
UPDATE policies p SET short_title = v.short FROM (VALUES
    ('^Transportation, Housing and Urban Development, and Related Agencies Appropriations Act, 2026', 'FY2026 consolidated appropriations (incl. DoD)'),
    ('^Notice of Determination .*Section 301: Brazil', 'Section 301: Brazil trade practices (comment request)'),
    ('^Seeking Comment on Prohibiting .*Military-Grade', 'Ban on foreign military-grade and swarming drones (comment request)'),
    ('^(Seeking Comment|Comment Sought) on Prohibiting .*Section 1709', 'Ban on FY2025 NDAA Sec. 1709 covered drones (comment request)'),
    ('^Notice of Rescission .*DJI Mavic 3', 'Rescinded over-people approval: DJI Mavic 3M with parachute'),
    ('^Implementing Certain Tariff-Related Elements .*Taiwan', 'Tariff elements of U.S.-Taiwan trade and security agreement'),
    ('^Implementing Certain Tariff-Related Elements .*Switzerland', 'Tariff elements of U.S.-Switzerland-Liechtenstein trade framework'),
    ('^Implementing Certain Tariff-Related Elements .*U\.S\.-EU', 'Tariff elements of U.S.-EU reciprocal trade framework'),
    ('^Implementing Certain Tariff-Related Elements .*Korea', 'Tariff elements of U.S.-Korea trade and investment deal'),
    ('^Notice of Request .*Section 232 .*Unmanned Aircraft', 'Section 232 investigation: drone imports (comment request)'),
    ('^Notice of Request .*Section 232 .*Robotics', 'Section 232 investigation: robotics imports (comment request)'),
    ('^A bill to require a report .*Drone Center of Excellence', 'Report on an Army National Guard Drone Center of Excellence'),
    ('^Protecting Against National Security Threats .*Equipment Authorization', 'FCC equipment authorization: supply chain threats'),
    ('^Federal Acquisition Regulation: Revolutionary', 'FAR overhaul (Parts 1, 2, 4, 33, 39, 40, 53)'),
    ('^Federal Acquisition Regulation: Prohibition on Certain Semiconductor', 'FAR: ban on certain semiconductor products'),
    ('^Adjusting Imports of Unmanned Aircraft Systems', 'Section 232 action on drone imports'),
    ('^Securing the Information and Communications Technology .*Unmanned', 'ICTS supply chain rule: drones'),
    ('^Enhanced Favorable Treatment for the United Arab Emirates', 'Eased export controls for the UAE'),
    ('^Measures To Restrict Stockpiling of Polysilicon', 'Polysilicon stockpiling limits'),
    ('^National Defense Authorization Act for Fiscal Year 2026', 'NDAA FY2026 (enacted)'),
    ('^National Defense Authorization Act for Fiscal Year 2027', 'NDAA FY2027 (H.R. 8800 / S. 4784)'),
    ('^Petition for Exemption; .*Agri Spray Drones', 'Exemption petition: Agri Spray Drones'),
    ('^Block the Use of Transatlantic Technology in Iranian', 'Block transatlantic tech in Iranian drones Act')
) AS v(pat, short) WHERE p.title ~ v.pat;

INSERT INTO schema_migrations (version) VALUES ('012_short_names');

COMMIT;
