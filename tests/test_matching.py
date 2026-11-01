import unittest

from internship_finder.matching import canonical_url, deduplicate, match_job
from internship_finder.models import RawJob


def vacancy(**changes):
    data = dict(company="Example Robotics", title="Software Engineering Internship", location="Paris, France",
                description="You will implement robot navigation components in C++.",
                url="https://example.test/jobs/123", source="Example employer", contract_type="Internship")
    data.update(changes)
    return RawJob(**data)


class MatchingTests(unittest.TestCase):
    def test_french_and_english_internships_with_cpp_responsibilities(self):
        examples = [
            vacancy(),
            vacancy(title="Stage ingénieur logiciel", contract_type="Stage", description="Vous développerez en C++."),
            vacancy(title="Stagiaire simulation", contract_type="", description="Conception de composants logiciels en C++."),
            vacancy(title="Software engineering", contract_type="", description="Contract type: Internship. Implement C++ components."),
        ]
        for job in examples:
            with self.subTest(title=job.title, description=job.description):
                accepted, evidence = match_job(job)
                self.assertTrue(accepted, evidence)
                self.assertTrue(evidence)
                self.assertEqual(evidence, job.metadata["cpp_evidence"])

    def test_permanent_apprenticeship_and_alternance_are_rejected(self):
        for contract in ["CDI", "Permanent", "Apprenticeship", "Apprentissage", "Alternance", "work-study"]:
            with self.subTest(contract=contract):
                self.assertFalse(match_job(vacancy(contract_type=contract))[0])
        self.assertFalse(match_job(vacancy(title="Alternant C++ software engineer"))[0])

    def test_description_explicitly_permanent_overrides_intern_title(self):
        job = vacancy(contract_type="", description="Contract type: Permanent. You will develop C++ components.")
        self.assertFalse(match_job(job)[0])

    def test_explicit_apprenticeship_sentence_overrides_intern_label(self):
        job = vacancy(description="This is a 12-month apprenticeship. You will implement components in C++.")
        self.assertFalse(match_job(job)[0])

    def test_possible_permanent_conversion_does_not_exclude_internship(self):
        job = vacancy(description="Develop C++ systems. Possibilité de CDI après le stage.")
        self.assertTrue(match_job(job)[0])

    def test_cpp_role_title_with_concrete_duties_and_required_skills(self):
        job = vacancy(title="Développeur C++ (H/F)", description="Develop trading systems. Required skill: C++.")
        self.assertTrue(match_job(job)[0])

    def test_incidental_interns_reference_is_not_an_internship(self):
        job = vacancy(title="Senior software engineer", contract_type="", description="Develop C++ systems and mentor interns.")
        self.assertFalse(match_job(job)[0])

    def test_cpp_only_as_optional_skill_does_not_establish_meaningful_work(self):
        for description in [
            "You will develop Python services. Familiarity with C++ is optional.",
            "Design Java software. C++ software skills would be a bonus.",
            "Développer une application Python. Une connaissance de C++ serait un plus.",
            "You will develop Python services, and familiarity with C++ is a bonus.",
            "You will develop Python services and C++ is a bonus.",
        ]:
            with self.subTest(description=description):
                self.assertFalse(match_job(vacancy(description=description))[0])

    def test_unrelated_cpp_mention_is_rejected(self):
        self.assertFalse(match_job(vacancy(title="Marketing internship", description="Sell our C++ course to students."))[0])

    def test_paris_hybrid_office_is_in_scope(self):
        job = vacancy(location="Paris 75002, France", work_arrangement="Hybrid")
        self.assertTrue(match_job(job)[0])

    def test_city_scope_excludes_other_paris_and_paris_saclay(self):
        for location in ["Paris, Texas, USA", "Paris, Ontario, Canada", "Paris-Saclay, France", "Paris Saclay, France", "Greater Paris", "Île-de-France"]:
            with self.subTest(location=location):
                self.assertFalse(match_job(vacancy(location=location), "paris")[0])

    def test_ile_de_france_expansion_and_configured_towns(self):
        for location in ["Paris, France", "Paris-Saclay, France", "Île-de-France", "92100, France", "Massy, France"]:
            with self.subTest(location=location):
                self.assertTrue(match_job(vacancy(location=location), "ile-de-france", ["Massy"])[0])
        self.assertFalse(match_job(vacancy(location="Lyon, France"), "ile-de-france", ["Massy"])[0])

    def test_invalid_scope_is_rejected(self):
        with self.assertRaises(ValueError):
            match_job(vacancy(), "france")


class DeduplicationTests(unittest.TestCase):
    def test_tracking_urls_are_same_listing(self):
        a = vacancy(url="https://example.test/jobs/123/?utm_campaign=one#apply", source="First")
        b = vacancy(url="https://EXAMPLE.test/jobs/123?utm_source=two", source="Second")
        result = deduplicate([a, b])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].metadata["discovered_on"], ["First", "Second"])

    def test_employer_original_preferred_over_aggregator(self):
        employer = vacancy(url="https://example.test/jobs/123", apply_url="https://example.test/apply/123", metadata={"employer_original": True})
        aggregator = vacancy(url="https://www.hellowork.com/fr-fr/emplois/999.html", apply_url=employer.apply_url,
                             description=employer.description * 3, source="HelloWork", metadata={"employer_original": False})
        result = deduplicate([aggregator, employer])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].url, employer.url)

    def test_same_requisition_id_survives_translated_titles_and_location(self):
        employer = vacancy(metadata={"requisition_id": "REQ-123"})
        aggregator = vacancy(title="Stage ingénieur logiciel C++", location="Paris 2e", url="https://www.hellowork.com/999", metadata={"requisition_id": "REQ-123"})
        self.assertEqual(len(deduplicate([employer, aggregator])), 1)

    def test_distinct_requisitions_are_not_merged_by_equal_title(self):
        a = vacancy(metadata={"requisition_id": "123"})
        b = vacancy(url="https://example.test/jobs/456", metadata={"requisition_id": "456"})
        self.assertEqual(len(deduplicate([a, b])), 2)

    def test_same_role_city_and_postal_address_mirrors_are_merged(self):
        a = vacancy(location="Paris, France")
        b = vacancy(location="75013 Paris, France", url="https://www.hellowork.com/999")
        self.assertEqual(len(deduplicate([a, b])), 1)

    def test_requisition_ids_are_scoped_to_company(self):
        a = vacancy(metadata={"requisition_id": "123"})
        b = vacancy(company="Another employer", url="https://other.test/jobs/123", metadata={"requisition_id": "123"})
        self.assertEqual(len(deduplicate([a, b])), 2)

    def test_url_connections_deduplicate_transitively(self):
        a = vacancy(title="English title", url="https://a.test/1", apply_url="https://ats.test/2")
        b = vacancy(title="Translated title", url="https://ats.test/2", apply_url="https://ats.test/3")
        c = vacancy(title="Third title", url="https://ats.test/3", apply_url="https://ats.test/4")
        self.assertEqual(len(deduplicate([a, b, c])), 1)


if __name__ == "__main__":
    unittest.main()
