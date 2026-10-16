@REC-5 @REC-9
Feature: Records manager reviews the migration queue
  As a records manager
  I want uncertain items routed to me with recommended dispositions
  So that nothing is migrated or disposed of without a recorded, valid decision

  Background:
    Given an analysed legacy library of 80 documents

  Scenario: Applying recommended dispositions clears the queue and is fully audited
    Given the review queue is not empty
    When the records manager "alice" applies the recommended dispositions
    Then the review queue is empty
    And every decision appears in the audit log
    And the audit chain verifies
    And the readiness verdict is "READY"

  Scenario: Unclassified items cannot be approved
    When the records manager tries to approve an unclassified item
    Then the decision is rejected with "reclassify it first"
    And the audit log has no entries

  Scenario: Readiness is blocked while items await review
    Then the readiness verdict is "NOT READY"
