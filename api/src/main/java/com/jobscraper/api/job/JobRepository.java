package com.jobscraper.api.job;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;

public interface JobRepository extends JpaRepository<Job, Long> {

    @Query("SELECT j FROM Job j WHERE " +
           "(:keyword IS NULL OR LOWER(j.title) LIKE LOWER(CONCAT('%', :keyword, '%'))) AND " +
           "(:remote IS NULL OR j.isRemote = :remote)")
    Page<Job> search(@Param("keyword") String keyword,
                     @Param("remote") Boolean remote,
                     Pageable pageable);
}
