@POL-2
Feature: Life underwriting rating
  Scenario Outline: Rating class from applicant risk
    When a <age> year old <smoking> <occupation> with income <income> applies for cover of <cover>
    Then the rating class is "<class>"

    Examples:
      | age | smoking    | occupation | income  | cover    | class       |
      | 35  | non-smoker | office     | 1000000 | 10000000 | preferred   |
      | 35  | smoker     | office     | 1000000 | 10000000 | substandard |
      | 35  | non-smoker | office     | 100000  | 10000000 | refer       |
      | 70  | non-smoker | office     | 5000000 | 10000000 | refer       |
